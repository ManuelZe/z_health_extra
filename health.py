##############################################################################
#
#    GNU Health HMIS: The Free Health and Hospital Information System
#    Copyright (C) 2008-2022 Luis Falcon <falcon@gnuhealth.org>
#    Copyright (C) 2011-2022 GNU Solidario <health@gnusolidario.org>
#    Copyright (C) 2015 Cédric Krier
#    Copyright (C) 2014-2015 Chris Zimmerman <siv@riseup.net>
#
#    The GNU Health HMIS component is part of the GNU Health project
#    www.gnuhealth.org
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
from decimal import Decimal
from collections import defaultdict, namedtuple
from itertools import combinations
from datetime import datetime, date

from num2words import num2words
from sql import Null
from sql.aggregate import Sum
from sql.conditionals import Coalesce, Case
from sql.functions import Round
import time
from datetime import datetime
import json
import requests
from trytond.i18n import gettext
from trytond.model import Workflow, ModelView, ModelSQL, fields, \
    sequence_ordered, Unique, DeactivableMixin, dualmethod
from trytond.model.exceptions import AccessError
from trytond.report import Report
from trytond.wizard import Wizard, StateView, StateTransition, StateAction, \
    Button
from trytond import backend
from trytond.pyson import If, Eval, Bool
from trytond.tools import reduce_ids, grouped_slice, firstline
from trytond.transaction import Transaction
from trytond.pool import Pool
from trytond.rpc import RPC
from trytond.config import config

import psycopg2
import string
import random
import requests
import json

from trytond.model import fields
from trytond.pool import PoolMeta
from trytond.modules.account.tax import TaxableMixin
from trytond.modules.product import price_digits

from .exceptions import (
    InvoiceTaxValidationError, InvoiceNumberError, InvoiceValidationError,
    InvoiceLineValidationError, PayInvoiceError, InvoicePaymentTermDateWarning)


class Insurance(metaclass=PoolMeta):
    'Insurance'
    __name__ = 'gnuhealth.insurance'
    _rec_name = 'number'

    employeur = fields.Many2One('party.party','Employeur', required=False, select=True,
        domain=[('is_institution', '=', True)])
    bpc = fields.Char('N BPC', required=False)
    plafond = fields.Numeric('Plafond', required=False)

    assureur = fields.Many2One(
        'party.party', 'Assureur(WTW)',
        required=False, select=True,
        domain=[('is_insurance_company', '=', True)])
    
    date_em = fields.Date("Date d'émmission")

    z_couverture = fields.Numeric("Couverture", digits=(3, 2), help="La couverture",
                                  required=False)
    


class Invoice(metaclass=PoolMeta):
    __name__ = "account.invoice"


    montant_assurance = fields.Function(fields.Numeric('Montant Assurance', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                'get_amount_with_insurance', searcher='search_total_amount_with_insurance')

    montant_patient = fields.Function(fields.Numeric('Montant Client', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                'get_amount_with_insurance', searcher='search_total_amount_with_insurance')
    
    dernier_versement = fields.Function(fields.Numeric('Dernier Versement', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                               'get_amount_with_insurance')

    total_amount2 = fields.Function(fields.Numeric('Total Avec Assurance', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                                    'get_amount_with_insurance', searcher='search_total_amount_with_insurance')

    
    @classmethod
    def search_total_amount_with_insurance(cls, name, clause):
        pool = Pool()
        Rule = pool.get('ir.rule')
        Line = pool.get('account.invoice.line')
        Tax = pool.get('account.invoice.tax')
        Invoice = pool.get('account.invoice')
        Currency = pool.get('currency.currency')
        type_name = cls.total_amount._field.sql_type().base
        line = Line.__table__()
        invoice = Invoice.__table__()
        currency = Currency.__table__()
        tax = Tax.__table__()

        _, operator, value = clause
        invoice_query = Rule.query_get('account.invoice')
        Operator = fields.SQL_OPERATORS[operator]
        # SQLite uses float for sum
        if value is not None and backend.name == 'sqlite':
            value = float(value)

        union = (line.join(invoice, condition=(invoice.id == line.invoice)
                ).join(currency, condition=(currency.id == invoice.currency)
                ).select(line.invoice.as_('invoice'),
                Coalesce(Sum(Round((line.quantity * line.unit_price).cast(
                                type_name),
                                currency.digits)), 0).as_('total_amount'),
                where=line.invoice.in_(invoice_query),
                group_by=line.invoice)
            | tax.select(tax.invoice.as_('invoice'),
                Coalesce(Sum(tax.amount), 0).as_('total_amount'),
                where=tax.invoice.in_(invoice_query),
                group_by=tax.invoice))
        query = union.select(union.invoice, group_by=union.invoice,
            having=Operator(Sum(union.total_amount).cast(type_name),
                value))
        return [('id', 'in', query)]

    @classmethod
    def get_amount_with_insurance(cls, invoices, names):
        pool = Pool()
        InvoiceTax = pool.get('account.invoice.tax')
        Move = pool.get('account.move')
        MoveLine = pool.get('account.move.line')
        cursor = Transaction().connection.cursor()

        untaxed_amount = dict((i.id, Decimal(0)) for i in invoices)
        tax_amount = dict((i.id, Decimal(0)) for i in invoices)
        total_amount = dict((i.id, Decimal(0)) for i in invoices)
        dernier_versement = dict((i.id, Decimal(0)) for i in invoices)
        montant_patient = dict((i.id, Decimal(0)) for i in invoices)
        montant_assurance = dict((i.id, Decimal(0)) for i in invoices)
        total_amount2 = dict((i.id, Decimal(0)) for i in invoices)

        type_name = cls.tax_amount._field.sql_type().base
        tax = InvoiceTax.__table__()
        to_round = False
        for sub_ids in grouped_slice(invoices):
            red_sql = reduce_ids(tax.invoice, sub_ids)
            cursor.execute(*tax.select(tax.invoice,
                    Coalesce(Sum(tax.amount), 0).as_(type_name),
                    where=red_sql,
                    group_by=tax.invoice))
            for invoice_id, sum_ in cursor:
                # SQLite uses float for SUM
                if not isinstance(sum_, Decimal):
                    sum_ = Decimal(str(sum_))
                    to_round = True
                tax_amount[invoice_id] = sum_
        # Float amount must be rounded to get the right precision
        if to_round:
            for invoice in invoices:
                tax_amount[invoice.id] = invoice.currency.round(
                    tax_amount[invoice.id])

        invoices_move = set()
        invoices_no_move = set()
        for invoice in invoices:
            if invoice.move:
                invoices_move.add(invoice.id)
            else:
                invoices_no_move.add(invoice.id)
        invoices_move = cls.browse(invoices_move)
        invoices_no_move = cls.browse(invoices_no_move)

        type_name = cls.total_amount._field.sql_type().base
        invoice = cls.__table__()
        move = Move.__table__()
        line = MoveLine.__table__()
        to_round = False
        for sub_ids in grouped_slice(invoices_move):
            red_sql = reduce_ids(invoice.id, sub_ids)
            cursor.execute(*invoice.join(move,
                    condition=invoice.move == move.id
                    ).join(line, condition=move.id == line.move
                    ).select(invoice.id,
                    Coalesce(Sum(
                            Case((
                                    line.second_currency == invoice.currency,
                                    line.amount_second_currency),
                                else_=line.debit - line.credit)),
                        0).cast(type_name),
                    where=(invoice.account == line.account) & red_sql,
                    group_by=invoice.id))
            for invoice_id, sum_ in cursor:
                # SQLite uses float for SUM
                if not isinstance(sum_, Decimal):
                    sum_ = Decimal(str(sum_))
                    to_round = True
                total_amount[invoice_id] = sum_

        for invoice in invoices_move:
            if invoice.type == 'in':
                total_amount[invoice.id] *= -1
            # Float amount must be rounded to get the right precision
            if to_round:
                total_amount[invoice.id] = invoice.currency.round(
                    total_amount[invoice.id])
            untaxed_amount[invoice.id] = (
                total_amount[invoice.id] - tax_amount[invoice.id])
            
            if invoice.health_service != None:
                if invoice.health_service.insurance_plan != None:
                    if invoice.health_service.insurance_plan.plafond != None and total_amount[invoice.id] > Decimal(invoice.health_service.insurance_plan.plafond):
                        montant_patient[invoice.id] = total_amount[invoice.id] - Decimal(invoice.health_service.insurance_plan.plafond)
                        montant_assurance[invoice.id] = Decimal(invoice.health_service.insurance_plan.plafond)
                        total_amount[invoice.id] = montant_patient[invoice.id]
                    elif invoice.health_service.insurance_plan.plafond != None  and total_amount[invoice.id] < Decimal(invoice.health_service.insurance_plan.plafond):
                        montant_patient[invoice.id] = Decimal(0)
                        montant_assurance[invoice.id] = total_amount[invoice.id]
                        total_amount[invoice.id] = montant_assurance[invoice.id]
            else :
                montant_patient[invoice.id] = total_amount[invoice.id]
                montant_assurance[invoice.id] = Decimal(0)

            if invoice.payment_lines :
                dernier_versement[invoice.id] = invoice.payment_lines[len(invoice.payment_lines) - 1].credit

                        

        for invoice in invoices_no_move:
            untaxed_amount[invoice.id] = sum(
                (line.amount for line in invoice.lines
                    if line.type == 'line'), Decimal(0))
            total_amount[invoice.id] = (
                untaxed_amount[invoice.id] + tax_amount[invoice.id])
            
            if invoice.health_service != None:
                if invoice.health_service.insurance_plan != None:
                    if invoice.health_service.insurance_plan.plafond != None and total_amount[invoice.id] > Decimal(invoice.health_service.insurance_plan.plafond):
                        montant_patient[invoice.id] = total_amount[invoice.id] - Decimal(invoice.health_service.insurance_plan.plafond)
                        montant_assurance[invoice.id] = Decimal(invoice.health_service.insurance_plan.plafond)
                        total_amount[invoice.id] = montant_patient[invoice.id]
                    elif invoice.health_service.insurance_plan.plafond != None  and total_amount[invoice.id] < Decimal(invoice.health_service.insurance_plan.plafond):
                        montant_patient[invoice.id] = Decimal(0)
                        montant_assurance[invoice.id] = total_amount[invoice.id]
                        total_amount[invoice.id] = montant_assurance[invoice.id]
            else :
                montant_patient[invoice.id] = total_amount[invoice.id]
                montant_assurance[invoice.id] = Decimal(0)

        result = {
            'untaxed_amount': untaxed_amount,
            'tax_amount': tax_amount,
            'total_amount': total_amount,
            'montant_patient' : montant_patient,
            'montant_assurance' : montant_assurance,
            'dernier_versement' :dernier_versement,
            }
        for key in list(result.keys()):
            if key not in names:
                del result[key]
        return result
    
    
    def get_move(self):
        '''
        Compute account move for the invoice and return the created move
        '''
        pool = Pool()
        Move = pool.get('account.move')
        Period = pool.get('account.period')
        Date = pool.get('ir.date')
        Warning = pool.get('res.user.warning')
        Lang = pool.get('ir.lang')

        today = Date.today()

        if self.move:
            return self.move
        self.update_taxes(exception=True)
        move_lines = []
        for line in self.lines:
            move_lines += line.get_move_lines_with_assurance()
        for tax in self.taxes:
            move_lines += tax.get_move_lines_with_assurance()

        total = sum(l.debit - l.credit for l in move_lines)
        if self.payment_term:
            payment_date = self.payment_term_date or self.invoice_date
            term_lines = self.payment_term.compute(
                total, self.company.currency, payment_date)
        else:
            term_lines = [(self.payment_term_date or today, total)]
        if self.currency != self.company.currency:
            remainder_total_currency = self.total_amount.copy_sign(total)
        else:
            remainder_total_currency = 0
        past_payment_term_dates = []
        for date, amount in term_lines:
            line = self._get_move_line(date, amount)
            if line.amount_second_currency:
                remainder_total_currency += line.amount_second_currency
            move_lines.append(line)
            if self.type == 'out' and date < today:
                past_payment_term_dates.append(date)
        if any(past_payment_term_dates):
            lang = Lang.get()
            warning_key = 'invoice_payment_term_%d' % self.id
            if Warning.check(warning_key):
                raise InvoicePaymentTermDateWarning(warning_key,
                    gettext('account_invoice'
                        '.msg_invoice_payment_term_date_past',
                        invoice=self.rec_name,
                        date=lang.strftime(min(past_payment_term_dates))))
        if not self.currency.is_zero(remainder_total_currency):
            move_lines[-1].amount_second_currency -= \
                remainder_total_currency

        accounting_date = self.accounting_date or self.invoice_date
        period_id = Period.find(self.company.id, date=accounting_date)

        move = Move()
        move.journal = self.journal
        move.period = period_id
        move.date = accounting_date
        move.origin = self
        move.company = self.company
        move.lines = move_lines
        print("Essayons de regarder le move ----------------------------- ", move)
        return move


class InvoiceLine(metaclass=PoolMeta):
    'Invoice Line'
    __name__ = 'account.invoice.line'

    def get_move_lines_with_assurance(self):
        '''
        Return a list of move lines instances for invoice line
        '''
        pool = Pool()
        Currency = pool.get('currency.currency')
        MoveLine = pool.get('account.move.line')
        if self.type != 'line':
            return []
        line = MoveLine()
        if self.invoice.currency != self.invoice.company.currency:
            with Transaction().set_context(date=self.invoice.currency_date):
                amount = Currency.compute(self.invoice.currency,
                    self.montant_patient, self.invoice.company.currency)
            line.amount_second_currency = self.amount
            line.second_currency = self.invoice.currency
        else:
            amount = self.montant_patient
            line.amount_second_currency = None
            line.second_currency = None
        
        print("Essayons de regarder le montant ----------------------------- ", amount)
        if amount >= 0:
            if self.invoice.type == 'out':
                line.debit, line.credit = 0, amount
            else:
                line.debit, line.credit = amount, 0
        else:
            if self.invoice.type == 'out':
                line.debit, line.credit = -amount, 0
            else:
                line.debit, line.credit = 0, -amount
        if line.amount_second_currency:
            line.amount_second_currency = (
                line.amount_second_currency.copy_sign(
                    line.debit - line.credit))
        line.account = self.account
        if self.account.party_required:
            line.party = self.invoice.party
        line.origin = self
        line.tax_lines = self._compute_taxes()

        print("Essayons de regarder les lignes ----------------------------- ", line)
        return [line]
    
    
class PayInvoice(Wizard):

    'Pay Invoice'
    __name__ = 'account.invoice.pay'
    start_with_assurance = StateView('account.invoice.pay.start',
        'account_invoice.pay_start_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('OK', 'choice', 'tryton-ok', default=True),
            ])
    choice_with_assurance = StateTransition()
    ask_with_assurance = StateView('account.invoice.pay.ask',
        'account_invoice.pay_ask_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('OK', 'pay', 'tryton-ok', default=True),
            ])
    pay = StateTransition()

    @classmethod
    def __setup__(cls):
        super(PayInvoice, cls).__setup__()
        cls.__rpc__['create'].fresh_session = True

    def get_reconcile_lines_for_amount(self, invoice, amount):
        if invoice.type == 'in':
            amount *= -1
        return invoice.get_reconcile_lines_for_amount(amount)

    def default_start_with_assurance(self, fields):
        default = {}
        invoice = self.record
        default['company'] = invoice.company.id
        default['currency'] = invoice.currency.id
        default['currency_digits'] = invoice.currency.digits
        default['amount'] = (invoice.amount_to_pay_today
            or invoice.amount_to_pay)
        default['invoice_account'] = invoice.account.id
        return default
    
    def transition_choice(self):
        pool = Pool()
        Currency = pool.get('currency.currency')

        invoice = self.record

        with Transaction().set_context(date=self.start.date):
            amount = Currency.compute(self.start.currency,
                self.start.amount, invoice.company.currency)
            amount_invoice = Currency.compute(
                self.start.currency, self.start.amount, invoice.currency)
        _, remainder = self.get_reconcile_lines_for_amount(invoice, amount)
        if (remainder == Decimal('0.0')
                and amount_invoice <= invoice.amount_to_pay):
            return 'pay'
        return 'ask'

    def default_ask(self, fields):
        pool = Pool()
        Currency = pool.get('currency.currency')

        default = {}
        invoice = self.record
        default['lines_to_pay'] = [x.id for x in invoice.lines_to_pay
                if not x.reconciliation]

        default['amount'] = self.start.amount
        default['date'] = self.start.date
        default['currency'] = self.start.currency.id
        default['currency_digits'] = self.start.currency_digits
        default['company'] = invoice.company.id

        with Transaction().set_context(date=self.start.date):
            amount = Currency.compute(self.start.currency,
                self.start.amount, invoice.company.currency)
            amount_invoice = Currency.compute(
                self.start.currency, self.start.amount, invoice.currency)

        if invoice.company.currency.is_zero(amount):
            lines = invoice.lines_to_pay
        else:
            lines, _ = self.get_reconcile_lines_for_amount(invoice, amount)
        default['lines'] = [x.id for x in lines]

        for line_id in default['lines'][:]:
            if line_id not in default['lines_to_pay']:
                default['lines'].remove(line_id)
        
        default['payment_lines'] = [x.id for x in invoice.payment_lines
                if not x.reconciliation]

        default['currency_writeoff'] = invoice.company.currency.id
        default['currency_digits_writeoff'] = invoice.company.currency.digits
        default['invoice'] = invoice.id

        if (amount_invoice > invoice.amount_to_pay
                or invoice.company.currency.is_zero(amount)):
            default['type'] = 'writeoff'
        return default

    def transition_pay(self):
        pool = Pool()
        Currency = pool.get('currency.currency')
        MoveLine = pool.get('account.move.line')
        Lang = pool.get('ir.lang')

        invoice = self.record

        with Transaction().set_context(date=self.start.date):
            amount = Currency.compute(self.start.currency,
                self.start.amount, invoice.company.currency)
            amount_invoice = Currency.compute(
                self.start.currency, self.start.amount, invoice.currency)

        reconcile_lines, remainder = \
            self.get_reconcile_lines_for_amount(invoice, amount)

        amount_second_currency = None
        second_currency = None
        if self.start.currency != invoice.company.currency:
            amount_second_currency = self.start.amount
            second_currency = self.start.currency
        
        overpayment = 0
        if (0 <= invoice.amount_to_pay < amount_invoice
                or amount_invoice < invoice.amount_to_pay <= 0):
            if self.ask.type == 'partial':
                lang = Lang.get()
                raise PayInvoiceError(
                    gettext('account_invoice'
                        '.msg_invoice_pay_amount_greater_amount_to_pay',
                        invoice=invoice.rec_name,
                        amount_to_pay=lang.currency(
                            invoice.amount_to_pay, invoice.currency)))
            else:
                overpayment = amount_invoice - invoice.amount_to_pay

        lines = []
        if not invoice.company.currency.is_zero(amount):
            lines = invoice.pay_invoice(amount,
                self.start.payment_method, self.start.date,
                self.start.description, amount_second_currency,
                second_currency, overpayment)

        if remainder:
            if self.ask.type != 'partial':
                to_reconcile = {l for l in self.ask.lines}
                to_reconcile.update(
                    l for l in invoice.payment_lines
                    if not l.reconciliation)
                if self.ask.type == 'writeoff':
                    to_reconcile.update(lines)
                if to_reconcile:
                    MoveLine.reconcile(
                        to_reconcile,
                        writeoff=self.ask.writeoff,
                        date=self.start.date)
        else:
            reconcile_lines += lines
            if reconcile_lines:
                MoveLine.reconcile(reconcile_lines)
        return 'end'