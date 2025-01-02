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
from num2words import num2words

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


class Lab(metaclass=PoolMeta):
    'Patient Lab Test Results'
    __name__ = 'gnuhealth.lab'

    renseignements = fields.Text('Renseignements Cliniques')
    macroscopie = fields.Text('Macroscopie')
    microscopie = fields.Text('Microscopie')

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

class PayInvoiceStart(metaclass=PoolMeta):
    'Pay Invoice'
    __name__ = 'account.invoice.pay.start'

    number = fields.Char("Number")
    amount_l = fields.Char('Lettre')

    @fields.depends('amount')
    def on_change_with_amount_l(self):
        return num2words(self.amount, lang='fr').capitalize()

class TestType(metaclass=PoolMeta):
    'Type of Lab test'
    __name__ = 'gnuhealth.lab.test_type'

    test_type = fields.Many2One(
        'gnuhealth.lab.type', 'Paillasse')

class LabTestType(ModelSQL, ModelView):
    'Lab Test Type'
    __name__ = 'gnuhealth.lab.type'

    code = fields.Char('Code', required=True)
    name = fields.Char('Name', required=True)

class Invoice(metaclass=PoolMeta):
    __name__ = "account.invoice"


    # montant_assurance = fields.Function(fields.Numeric('Montant Assurance', digits=(16,
    #             Eval('currency_digits', 2)), depends=['currency_digits']),
    #             'get_amount_with_insurance', searcher='search_total_amount_with_insurance')

    montant_patient = fields.Function(fields.Numeric('Montant Client', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                'get_amount_with_insurance', searcher='search_total_amount_with_insurance')
    
    dernier_versement = fields.Function(fields.Numeric('Dernier Versement', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                               'get_amount_with_insurance')

    total_amount2 = fields.Function(fields.Numeric('Total Avec Assurance', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                                    'get_amount_with_insurance', searcher='search_total_amount_with_insurance')
    
    montant_assurance = fields.Numeric('Montant Assurance', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits'], readonly=True)
    
    montant_en_lettre = fields.Char('Lettre')

    @fields.depends('dernier_versement')
    def on_change_with_montant_en_lettre(self):
        return num2words(self.amount, lang='fr').capitalize()
    
    @staticmethod
    def convert_letter(name):
        return num2words(name, lang='fr').capitalize()
    
    @staticmethod
    def calcul_prix_MSH(party, line):
        pool = Pool()
        # Liste de sortie
        # elt = [indice, prix_unitaire, qte, montant_total]
        elt = []
        elt.append(line.product.list_price)
        Product_Price_List = pool.get("product.price_list")
        sale_price_list = Product_Price_List.search([
                ('name', '=', 'Port Autonome de Douala'),
                ], limit=1)
        print("Sale Price List List --------------- ", sale_price_list[0])
        unit_price = sale_price_list[0].compute(
                            party,
                            line.product, line.product.list_price,
                            line.quantity, line.product.default_uom)
        print("Unit price price --------------- ", unit_price)
        elt.append(unit_price)
        elt.append(line.quantity)
        elt.append(elt[1]*Decimal(elt[2]))
        print("Les éléments --------------- ", elt)
        return elt
    
    @classmethod
    def calcul_prix_total_MSH(cls, record):

        total = 0
        for line in record.lines :
            elt = cls.calcul_prix_MSH(record.party, line)
            total = total + elt[3]
        return total


    # montant_patient = fields.Numeric('Montant Client', digits=(16,
    #             Eval('currency_digits', 2)), depends=['currency_digits'], readonly=True)
    
    # dernier_versement = fields.Numeric('Dernier Versement', digits=(16,
    #             Eval('currency_digits', 2)), depends=['currency_digits'], readonly=True)

    # total_amount2 = fields.Numeric('Total avec Assurance', digits=(16,
    #             Eval('currency_digits', 2)), depends=['currency_digits'], readonly=True)


    def on_change_agent(self, name):
        try:
            return self.lines[0].origin.name.agent.id
        except:
            return None
    
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

    @fields.depends('lines', 'taxes', 'currency',
        'accounting_date', 'invoice_date',  # From tax_date
        methods=['_get_taxes'])
    def _on_change_lines_taxes(self):
        pool = Pool()
        InvoiceTax = pool.get('account.invoice.tax')

        self.untaxed_amount = Decimal('0.0')
        self.tax_amount = Decimal('0.0')
        self.total_amount = Decimal('0.0')
        self.total_amount2 = Decimal('0.0')
        computed_taxes = {}

        if self.lines:
            for line in self.lines:
                self.untaxed_amount += getattr(line, 'amount', None) or 0
            computed_taxes = self._get_taxes()

        def is_zero(amount):
            if self.currency:
                return self.currency.is_zero(amount)
            else:
                return amount == Decimal('0.0')

        tax_keys = []
        taxes = list(self.taxes or [])
        for tax in (self.taxes or []):
            if tax.manual:
                self.tax_amount += tax.amount or Decimal('0.0')
                continue
            key = tax._key
            if (key not in computed_taxes) or (key in tax_keys):
                taxes.remove(tax)
                continue
            tax_keys.append(key)
            if not is_zero(computed_taxes[key]['base']
                    - (tax.base or Decimal('0.0'))):
                self.tax_amount += computed_taxes[key]['amount']
                tax.amount = computed_taxes[key]['amount']
                tax.base = computed_taxes[key]['base']
            else:
                self.tax_amount += tax.amount or Decimal('0.0')
        for key in computed_taxes:
            if key not in tax_keys:
                self.tax_amount += computed_taxes[key]['amount']
                value = InvoiceTax.default_get(
                    list(InvoiceTax._fields.keys()), with_rec_name=False)
                value.update(computed_taxes[key])
                invoice_tax = InvoiceTax(**value)
                if invoice_tax.tax:
                    invoice_tax.sequence = invoice_tax.tax.sequence
                taxes.append(invoice_tax)
        self.taxes = taxes
        if self.currency:
            self.untaxed_amount = self.currency.round(self.untaxed_amount)
            self.tax_amount = self.currency.round(self.tax_amount)
        self.total_amount2 = self.untaxed_amount + self.tax_amount
        self.total_amount = self.montant_patient
        if self.currency:
            self.total_amount2 = self.currency.round(self.total_amount)
            self.total_amount = self.currency_round(self.montant_patient)


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
                    if invoice.health_service.insurance_plan.plafond != None:
                        montant_patient[invoice.id] = total_amount[invoice.id]
            else :
                montant_patient[invoice.id] = total_amount[invoice.id]

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
                    if invoice.health_service.insurance_plan.plafond != None:
                        montant_patient[invoice.id] = total_amount[invoice.id]
            else :
                montant_patient[invoice.id] = total_amount[invoice.id]

            if invoice.payment_lines :
                dernier_versement[invoice.id] = invoice.payment_lines[len(invoice.payment_lines) - 1].credit

        total_amount2[invoice.id] = total_amount[invoice.id] + invoice.montant_assurance
        result = {
            'untaxed_amount': untaxed_amount,
            'tax_amount': tax_amount,
            'total_amount': total_amount,
            'total_amount2': total_amount2,
            'montant_patient' : montant_patient,
            'dernier_versement' :dernier_versement,
            }
        for key in list(result.keys()):
            if key not in names:
                del result[key]
        return result
    
    