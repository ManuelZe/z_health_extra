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
from trytond.pyson import PYSONEncoder
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
from trytond.modules.product import round_price

import psycopg2
import string
import random
import requests
import json

from trytond.model import fields
from trytond.pool import PoolMeta
from trytond.modules.account.tax import TaxableMixin
from trytond.modules.product import price_digits
from trytond.modules.health.core import get_health_professional

from .exceptions import (
    InvoiceTaxValidationError, ExploOrderExists, LabOrderExists, InvoiceNumberError, InvoiceValidationError,
    InvoiceLineValidationError, PayInvoiceError, InvoicePaymentTermDateWarning)


class Lab(metaclass=PoolMeta):
    'Patient Lab Test Results'
    __name__ = 'gnuhealth.lab'

    renseignements = fields.Text('Renseignements Cliniques')
    macroscopie = fields.Text('Macroscopie')
    microscopie = fields.Text('Microscopie')

    @staticmethod
    def afficher_unites_compactees(records):
        # Filtrer les unités non vides
        unites_remplies = [record.diagnosis for record in records if record.diagnosis]
        
        # Joindre les unités avec des points
        return "\n".join(unites_remplies)
    
    def get_analytes_summary(self, name):
        summ = ""
        for analyte in self.critearea:
            if analyte.result or analyte.result_text:
                res = ""
                res_text = ""
                if analyte.result_text:
                    res_text = analyte.result_text
                if analyte.result:
                    if analyte.units:
                        if analyte.units.name :
                            res = str(analyte.result) + \
                                " (" + analyte.units.name + ")  "
                    else :
                        res = str(analyte.result) + " "
                summ = summ + analyte.rec_name + "  " + \
                    res + res_text + "\n"
        return summ

    @staticmethod
    def listes_paillasses(records):
        
        liste_paillasse = []
        for record in records :
            if record.test.test_type.name :
                if record.test.test_type.name not in liste_paillasse:
                    liste_paillasse.append(record.test.test_type.name)

        return liste_paillasse

    @staticmethod
    def prescriptor_name(id):

        pool = Pool()
        Result = pool.get('gnuhealth.patient.lab.test')
        Results = Result.search([('request', '=', id)], limit=1)
        return Results[0].service.requestor.name.name+" "+Results[0].service.requestor.name.lastname
    

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
                                  required=True)

class PayInvoiceStart(metaclass=PoolMeta):
    'Pay Invoice'
    __name__ = 'account.invoice.pay.start'

    number = fields.Char("Number")
    montant_facture = fields.Numeric("Montant Du Patient", readonly=True)
    reste_payer = fields.Numeric("Reste à Payer", readonly=True)
    amount_l = fields.Char('Lettre', size=None)
    reste_payer_l = fields.Char('Reste à Payer en Lettre', size=None)

    

    @fields.depends('amount')
    def on_change_with_amount_l(self):
        return num2words(self.amount, lang='fr').capitalize()
    
    @fields.depends('reste_payer')
    def on_change_with_reste_payer_l(self):
        return num2words(self.reste_payer, lang='fr').capitalize()


class PayInvoice(metaclass=PoolMeta):
    'Pay Invoice'
    __name__ = 'account.invoice.pay'

    def transition_choice(self):
        pool = Pool()
        Currency = pool.get('currency.currency')

        invoice = self.record

        with Transaction().set_context(date=self.start.date):
            if self.start.amount < 0 :
                if self.start.amount > float(invoice.amount_to_pay_today or invoice.amount_to_pay)*(1-0.6):
                    raise PayInvoiceError(
                        message = "Le Montant doit être plus de 60% pour un premier paiement.",
                        description = "Vérifiez que le montant soit supérieur à 60% de la somme"
                    )
            else :
                if self.start.amount < float(invoice.amount_to_pay_today or invoice.amount_to_pay)*(1-0.6):
                    raise PayInvoiceError(
                        message = "Le Montant doit être plus de 60% pour un premier paiement.",
                        description = "Vérifiez que le montant soit supérieur à 60% de la somme"
                    )
            amount = Currency.compute(self.start.currency,
                self.start.amount, invoice.company.currency)
            amount_invoice = Currency.compute(
                self.start.currency, self.start.amount, invoice.currency)
        _, remainder = self.get_reconcile_lines_for_amount(invoice, amount)
        if (remainder == Decimal('0.0')
                and amount_invoice <= invoice.amount_to_pay):
            return 'pay'
        return 'ask'

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

class Commission(metaclass=PoolMeta):
    __name__ = "commission"

    is_validate = fields.Boolean("Validé ", help="Cette case est coché si cette commission a été validé ou pas.")

    def bordereau_commission(self, records):
        # exemplaire de sortie canevas
        # liste_prix = ["Montant_prime_ht", "taxe", "Net_a_payer"]
        liste_prix = []

        Montant_prime_ht = 0
        taxe = 0
        net_a_payer = 0
        for record in records :
            if liste_prix == []:
                Montant_prime_ht = record.amount
                taxe = (0.11*float(record.amount))
                net_a_payer = float(record.amount)-0.11*float(record.amount)
                liste_prix.append(Montant_prime_ht)
                liste_prix.append(taxe)
                liste_prix.append(net_a_payer)
            else :
                liste_prix[0] += record.amount
                liste_prix[1] += (0.11*float(record.amount))
                liste_prix[2] += float(record.amount)-0.11*float(record.amount)

        return liste_prix

class Invoice(metaclass=PoolMeta):
    __name__ = "account.invoice"


    # montant_assurance = fields.Function(fields.Numeric('Montant Assurance', digits=(16,
    #             Eval('currency_digits', 2)), depends=['currency_digits']),
    #             'get_amount_with_insurance', searcher='search_total_amount_with_insurance')

    montant_patient = fields.Function(fields.Numeric('Montant Client', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                'get_amount_with_insurance', searcher='search_total_amount_with_insurance')
    
    montant_verse = fields.Function(fields.Numeric('Montant Versé Par le Patient', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                               'get_amount_with_insurance')
    
    remboursement = fields.Function(fields.Numeric('Remboursement', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                               'get_amount_with_insurance')
    
    dernier_versement = fields.Function(fields.Numeric('Dernier Versement', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                               'get_amount_with_insurance')

    total_amount2 = fields.Function(fields.Numeric('Total Avec Assurance', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits']),
                                    'get_amount_with_insurance', searcher='search_total_amount_with_insurance')
    
    montant_assurance = fields.Numeric('Montant Assurance', digits=(16,
                Eval('currency_digits', 2)), depends=['currency_digits'], readonly=True)
    
    montant_en_lettre = fields.Char('Lettre')

    tarifaire = fields.Many2One('product.price_list','Tarifaire', required=False, select=True)

    @classmethod
    def credit(cls, invoices, refund=False, **values):
        '''
        Credit invoices and return ids of new invoices.
        Return the list of new invoice
        '''
        new_invoices = [i._credit(**values) for i in invoices]
        new_invoices = [i._credit(**{**values, 'montant_assurance': -(i.montant_assurance), 'reference': i.number}) for i in invoices]
        cls.save(new_invoices)
        cls.update_taxes(new_invoices)
        if refund:
            cls.post(new_invoices)
            for invoice, new_invoice in zip(invoices, new_invoices):
                if invoice.state != 'posted':
                    raise AccessError(
                        gettext('account_invoice'
                            '.msg_invoice_credit_refund_not_posted',
                            invoice=invoice.rec_name))
                invoice.cancel_move = new_invoice.move
            cls.save(invoices)
            cls.cancel(invoices)
        return new_invoices

    # @classmethod
    # def _post(cls, invoices):
    #     # Create commission only the first time the invoice is posted
    #     to_commission = [i for i in invoices
    #         if i.state not in ['posted', 'paid']]
    #     super()._post(invoices)
    #     cls.create_commissions(to_commission)

    @classmethod
    def contact(self, id):

        transaction = Transaction()
        cursor = transaction.connection.cursor()
        #cursor = conn2.cursor()
        f = cursor.execute("SELECT current_database()")
        database_name = cursor.fetchone()[0]
        #sql = "SELECT type, value FROM party_contact_mechanism JOIN party_party ON party_contact_mechanism.party = party_party.id JOIN gnuhealth_patient ON {} = gnuhealth_patient.id".format(id)
        sql1 = "SELECT name FROM gnuhealth_patient where gnuhealth_patient.id = {}".format(id)
        cursor.execute(sql1)
        data1 = cursor.fetchone()
        sql2 = "SELECT type, value FROM party_contact_mechanism WHERE party = {}".format(data1[0])
        cursor.execute(sql2)
        data2 = cursor.fetchall()
        #print("Les différentes données ", data2[0][0])

        list_phone = []
        for item in data2 :
            if item[0] == "phone" :
                list_phone.append(item[1])

        return ("/ ".join(list_phone))
    
    @classmethod
    def contact2(self, id):

        transaction = Transaction()
        cursor = transaction.connection.cursor()
        #cursor = conn2.cursor()
        f = cursor.execute("SELECT current_database()")
        database_name = cursor.fetchone()[0]
        #sql = "SELECT type, value FROM party_contact_mechanism JOIN party_party ON party_contact_mechanism.party = party_party.id JOIN gnuhealth_patient ON {} = gnuhealth_patient.id".format(id)
        sql2 = "SELECT type, value FROM party_contact_mechanism WHERE party = {}".format(id)
        cursor.execute(sql2)
        data2 = cursor.fetchall()
        #print("Les différentes données ", data2[0][0])

        list_phone = []
        for item in data2 :
            if item[0] == "phone" :
                list_phone.append(item[1])

        return ("/ ".join(list_phone))

    @classmethod
    def create_commissions(cls, invoices):
        pool = Pool()
        Commission = pool.get('commission')
        # Enlever ceci après la fin des travaux
        all_commissions = []
        for invoice in invoices:
            for line in invoice.lines:
                commissions = line.get_commissions()
                if commissions:
                    all_commissions.extend(commissions)

        Commission.save(all_commissions)
        return all_commissions

    @staticmethod
    def lab_requests2(reference):
        LabTest = Pool().get('gnuhealth.patient.lab.test')
        TestRequest = Pool().get('gnuhealth.patient.lab.test')
        Lab = Pool().get('gnuhealth.lab')

        tests_report_data = []

        tests = TestRequest.search([('service.name', '=', reference)])

        for lab_test_order in tests:

            test_cases = []
            test_report_data = {}

            if lab_test_order.state == 'ordered':
                break

            test_report_data['test'] = lab_test_order.name.id
            test_report_data['patient'] = lab_test_order.patient_id.id
            if lab_test_order.doctor_id:
                test_report_data['requestor'] = lab_test_order.doctor_id.id
            test_report_data['date_requested'] = lab_test_order.date
            test_report_data['request_order'] = lab_test_order.request

            for critearea in lab_test_order.name.critearea:
                test_cases.append(('create', [{
                        'name': critearea.name,
                        'sequence': critearea.sequence,
                        'lower_limit': critearea.lower_limit,
                        'upper_limit': critearea.upper_limit,
                        'normal_range': critearea.normal_range,
                        'units': critearea.units and critearea.units.id,
                    }]))
            test_report_data['critearea'] = test_cases

            tests_report_data.append(test_report_data)

        with Transaction().new_transaction():
            Lab.create(tests_report_data)
            TestRequest.write(tests, {'state': 'ordered'})
        return LabTest.search([('service.name', '=', reference)])
    
    @staticmethod
    def img_requests2(reference):
        ImagingRequest = Pool().get('gnuhealth.imaging.test.request')
        Request = Pool().get('gnuhealth.imaging.test.request')
        Result = Pool().get('gnuhealth.imaging.test.result')
        action = []
        request_data = []
        requests = Request.search([('service.name', '=', reference)])
        for request in requests:
            request_data.append({
                'patient': request.patient.id,
                'date': datetime.now(),
                'request_date': request.date,
                'requested_test': request.requested_test,
                'request': request.id,
                'order': request.request,
                'doctor': request.doctor})
        with Transaction().new_transaction():
            results = Result.create(request_data)

        action['pyson_domain'] = PYSONEncoder().encode(
            [('id', 'in', [r.id for r in results])])

        with Transaction().new_transaction():
            Request.requested(requests)
            Request.done(requests)
        return ImagingRequest.search([('service.name', '=', reference)])
    
    @staticmethod
    def exp_requests2(reference):
        ExpTest = Pool().get('gnuhealth.patient.exp.test')
        TestRequest = Pool().get('gnuhealth.patient.exp.test')
        Explo = Pool().get('gnuhealth.exp')
        tests_report_data = []
        tests = TestRequest.search([('service.name', '=', reference)]) 
        for explo_test_order in tests:

            test_cases = []
            test_report_data = {}

            if explo_test_order.state == 'ordered':
                break

            test_report_data['test'] = explo_test_order.name.id
            test_report_data['source_type'] = explo_test_order.source_type
            test_report_data['patient'] = explo_test_order.patient_id and explo_test_order.patient_id.id
            test_report_data['other_source'] = explo_test_order.other_source
            if explo_test_order.doctor_id:
                test_report_data['requestor'] = explo_test_order.doctor_id.id
            test_report_data['date_requested'] = explo_test_order.date
            test_report_data['request_order'] = explo_test_order.request

            for critearea in explo_test_order.name.critearea:
                test_cases.append(('create', [{
                        'name': critearea.name,
                        'code': critearea.code,
                        'sequence': critearea.sequence,
                        'lower_limit': critearea.lower_limit,
                        'upper_limit': critearea.upper_limit,
                        'normal_range': critearea.normal_range,
                        'units': critearea.units and critearea.units.id,
                    }]))
            test_report_data['critearea'] = test_cases

            tests_report_data.append(test_report_data)

        with Transaction().new_transaction():
            Explo.create(tests_report_data)
            TestRequest.write(tests, {'state': 'ordered'})

        return ExpTest.search([('service.name', '=', reference)])    


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
                ('name', '=', 'PORT AUTONOME DE DOUALA'),
                ], limit=1)
        unit_price = sale_price_list[0].compute(
                            party,
                            line.product, line.product.list_price,
                            line.quantity, line.product.default_uom)
        elt.append(unit_price)
        elt.append(line.quantity)
        elt.append(elt[1]*Decimal(elt[2]))
        return elt
    
    @classmethod
    def calcul_prix_total_MSH(cls, record):

        total = 0
        for line in record.lines :
            elt = cls.calcul_prix_MSH(record.party, line)
            total = total + elt[3]
        return total
    
    @classmethod
    def calcul_prix_preferentiel_MSH(cls, total, record=None):

        liste_prix = []
        total_pref = float(0.9)*float(total)
        liste_prix.append(total_pref)
        montant_a_payer = total_pref - cls.affichage_assurance(record.montant_patient)
        liste_prix.append(montant_a_payer)
        return liste_prix


    def total_synth_facture_assurance(self, records):
        return self._total_synth_facture_filtered(records, insurance=True)

    def total_synth_facture_pdmd(self, records):
        return self._total_synth_facture_filtered(records, insurance=False)
    
    def total_synth_facture_credit(self, records):
        elements = []
        total_amount = Decimal(0)
        montant_assurance = Decimal(0)
        z_remise2 = Decimal(0)
        net_a_payer = Decimal(0)
        amount_to_pay = Decimal(0)
        difference = Decimal(0)
        total_amount2 = Decimal(0)

        for record in records:
            if record.health_service :
                continue
            total_amount += record.untaxed_amount or Decimal(0)
            montant_assurance += record.montant_assurance or Decimal(0)
            net_a_payer += record.montant_patient or Decimal(0)
            amount_to_pay += record.amount_to_pay or Decimal(0)
            total_amount2 += Decimal(record.untaxed_amount or 0) + Decimal(record.montant_assurance or 0)
        
        difference = net_a_payer - amount_to_pay
        
        elements.extend([
            total_amount,
            montant_assurance,
            z_remise2,
            net_a_payer,
            difference,
            amount_to_pay,
            total_amount2
        ])
        
        return elements

    def _total_synth_facture_filtered(self, records, insurance=True):
        # Exemplaire de sortie de liste 
        # elements2 = ["total_amount", "montant_assurance", "Remise",  "montant_patient-amount_to_pay", "montant_patient", "amount_to_pay"]
        # elements = ["total_amount" , "montant_assurance", "montant_patient", "montant_patient-amount_to_pay", "amount_to_pay"]

        elements = []
        total_amount = Decimal(0)
        montant_assurance = Decimal(0)
        z_remise2 = Decimal(0)
        net_a_payer = Decimal(0)
        amount_to_pay = Decimal(0)
        difference = Decimal(0)
        total_amount2 = Decimal(0)

        for record in records:
            if record.health_service :
                if bool(record.health_service.insurance_plan) != insurance:
                    continue
                total_amount += record.untaxed_amount or Decimal(0)
                montant_assurance += record.montant_assurance or Decimal(0)
                z_remise2 += record.health_service.z_remise2 or Decimal(0)
                net_a_payer += record.montant_patient or Decimal(0)
                amount_to_pay += record.amount_to_pay or Decimal(0)
                total_amount2 += Decimal(record.untaxed_amount or 0) + Decimal(record.montant_assurance or 0)
        
        difference = net_a_payer - amount_to_pay
        
        elements.extend([
            total_amount,
            montant_assurance,
            z_remise2,
            net_a_payer,
            difference,
            amount_to_pay,
            total_amount2
        ])
        
        return elements
    
    def facture_reelles(self, records):
        list_of_save_elements = []
        listes_factures = []
        for Facture in records:
            if Facture.number not in listes_factures:
                listes_factures.append(Facture.number)
        
        for Facture in records:
            if Facture.reference in listes_factures:
                listes_factures.remove(Facture.reference)
                listes_factures.remove(Facture.number)

        for elt in listes_factures:
            Invoices = Pool().get("account.invoice")
            facture = Invoices.search([('number', '=', elt)], limit=1)
            list_of_save_elements.append(facture[0])

        return list_of_save_elements
    

    def total_facture_par_produits(self, records):
        # Exemplaire de sortie de liste 
        # elements = ["total_amount" , "montant_assurance", "montant_patient", "montant_patient-amount_to_pay", "amount_to_pay"]
        quantity = float(0)
        unit_price = float(0)
        amount = float(0)
        elements = []
        for record in records :
            for line in record.lines :
                quantity += float(line.quantity)
                unit_price += float(line.montant_produit())
                amount += quantity*unit_price
        
        elements.extend([unit_price, amount, quantity])
        
        return elements

    @staticmethod
    def format_nombre(n):
        """
        Formate un nombre en ajoutant des séparateurs de milliers.
        
        :param n: int - Le nombre à formater
        :return: str - Le nombre formaté avec des virgules comme séparateurs de milliers
        """
        return f"{n:,}"
    
    @staticmethod
    def affichage_assurance(n) :

        if float(n) <= float(20) :
            return 0.0
        else :
            return n

    def commission_Banque(self, records):

        liste_docteurs = {}
        for record in records:
            if record.party.clef:
                if len(record.party.clef) != 0 :
                    docteur = record.party.name+" "+record.party.lastname
                    list_element = []
                    for line in record.lines:
                        if docteur in liste_docteurs.keys():
                            liste_docteurs[docteur][0] = int(liste_docteurs[docteur][0] + record.total_amount)
                            liste_docteurs[docteur][1] = record.party.numero_carte
                            liste_docteurs[docteur][2] = record.party.clef
                        else:
                            list_element.append(int(record.total_amount))
                            list_element.append(record.party.numero_carte)
                            list_element.append(record.party.clef)
                            liste_docteurs[docteur] = list_element

        sorted_items = sorted(liste_docteurs.items(), key=lambda x: x[0])

        return dict(sorted_items)
        

    def commission_docteur(self, records):
        # Le modèle de sortie de la liste des docteurs : 
        # {"JUDITH": (montant, impot, net_a_payer), "FRED": (montant, impot, net_a_payer), "MARINA": (montant, impot, net_a_payer)}
        liste_docteurs = {}
        for record in records:
            if record.party.clef == None or record.party.clef == "":
                docteur = record.party.name+" "+record.party.lastname
                list_element = []
                for line in record.lines:
                    if docteur in liste_docteurs.keys():
                        liste_docteurs[docteur][0] = int(liste_docteurs[docteur][0] + line.unit_price)
                        liste_docteurs[docteur][1] = int(liste_docteurs[docteur][1] + (0.11*float(line.unit_price)))
                        liste_docteurs[docteur][2] = int(liste_docteurs[docteur][2] + line.amount)
                        liste_docteurs[docteur][3] = self.contact2(id=record.party.id)
                    else:
                        list_element.append(int(line.unit_price))
                        list_element.append(int(0.11*float(line.unit_price)))
                        list_element.append(int(line.amount))
                        list_element.append(self.contact2(id=record.party.id))
                        liste_docteurs[docteur] = list_element
            
        totaux = [0] * len(next(iter(liste_docteurs.values())))  # Crée une liste de zéros de la même longueur que les listes

        # Calcul des totaux
        for valeurs in liste_docteurs.values():
            for i in range(len(valeurs)-1):
                totaux[i] += valeurs[i]

        sorted_items = sorted(liste_docteurs.items(), key=lambda x: x[0])
        dict_trie = dict(sorted_items)
        # Ajouter le total au dictionnaire
        liste_docteurs["TOTAL"] = totaux
        cle, valeur = list(liste_docteurs.items())[-1]
        dict_trie["TOTAL"] = totaux

        return dict_trie
    
    def all_commission_docteur(self, records):
        # Le modèle de sortie de la liste des docteurs : 
        # {"JUDITH": (montant, impot, net_a_payer), "FRED": (montant, impot, net_a_payer), "MARINA": (montant, impot, net_a_payer)}
        liste_docteurs = {}
        for record in records:
            docteur = record.party.name+" "+record.party.lastname
            list_element = []
            for line in record.lines:
                if docteur in liste_docteurs.keys():
                    liste_docteurs[docteur][0] = int(liste_docteurs[docteur][0] + line.unit_price)
                    liste_docteurs[docteur][1] = int(liste_docteurs[docteur][1] + (0.11*float(line.unit_price)))
                    liste_docteurs[docteur][2] = int(liste_docteurs[docteur][2] + line.amount)
                    liste_docteurs[docteur][3] = self.contact2(id=record.party.id)
                else:
                    list_element.append(int(line.unit_price))
                    list_element.append(int(0.11*float(line.unit_price)))
                    list_element.append(int(line.amount))
                    list_element.append(self.contact2(id=record.party.id))
                    liste_docteurs[docteur] = list_element
            
        totaux = [0] * len(next(iter(liste_docteurs.values())))  # Crée une liste de zéros de la même longueur que les listes

        # Calcul des totaux
        for valeurs in liste_docteurs.values():
            for i in range(len(valeurs)-1):
                totaux[i] += valeurs[i]

        sorted_items = sorted(liste_docteurs.items(), key=lambda x: x[0])
        dict_trie = dict(sorted_items)
        # Ajouter le total au dictionnaire
        liste_docteurs["TOTAL"] = totaux
        cle, valeur = list(liste_docteurs.items())[-1]
        dict_trie["TOTAL"] = totaux

        return dict_trie


    def total_medecin(self, records):
        # Exemplaire de sortie de liste 
        # elements = ["montant" , "Impot", "net_a_payer"]
        
        elements = []
        for record in records :

            amount = sum(line.unit_price for line in record.lines)
            elements.append(amount)
            net_a_payer = sum(line.amount for line in record.lines)
            elements.append(net_a_payer)
            impots = amount - net_a_payer
            elements.append(impots)

        return elements

    
    def on_change_agent(self, name):
        try:
            return self.lines[0].origin.name.agent.id
        except:
            return None

    def montant_assurance_facture(self, montant_assurance, montant_total) :
        if montant_assurance > montant_total:
            return montant_total
        else:
            return montant_assurance

    def montant_recu(self, record):
        # Record corespond au recu
        # Format de la liste [prix1, prix2, prix3, prix4, prix5, total]

        sale_price_list = None
        if hasattr(record, 'tarifaire'):
            sale_price_list = record.tarifaire

        if sale_price_list == None and hasattr(record.party, 'sale_price_list'):
            sale_price_list = record.party.sale_price_list

        liste_montants = []
        for line in record.lines:
            unit_price = Decimal(0)
            if sale_price_list : 
                unit_price = sale_price_list.compute(
                             record.party,
                             line.product, line.product.list_price,
                             line.quantity, line.product.default_uom)

                liste_montants.append(float(unit_price)*line.quantity)
        
        total_recu = sum(liste_montants)

        liste_montants.append(total_recu)

        return liste_montants
    
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
            self.total_amount = self.currency.round(self.montant_patient)

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
        montant_verse = dict((i.id, Decimal(0)) for i in invoices)
        remboursement = dict((i.id, Decimal(0)) for i in invoices)
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
                    montant_patient[invoice.id] = total_amount[invoice.id]
            else :
                montant_patient[invoice.id] = total_amount[invoice.id]

            if invoice.payment_lines :
                next_lines_moves = 1
                montant_verse[invoice.id] = 0
                remboursement[invoice.id] = 0
                i = 0
                while next_lines_moves != 0 :
                    next_lines_moves = MoveLine.search([('id', '=', invoice.payment_lines[len(invoice.payment_lines) -1].id+i)], limit=1)
                    if next_lines_moves and next_lines_moves[0].credit != 0 :
                        next_lines_moves = next_lines_moves[0].credit
                    else :
                        montant_verse[invoice.id] = next_lines_moves = MoveLine.search([('id', '=', invoice.payment_lines[len(invoice.payment_lines) -1].id+i)], limit=1)[0].debit
                        next_lines_moves = 0
                    i = i+1
                dernier_versement[invoice.id] = invoice.payment_lines[len(invoice.payment_lines) - 1].credit  
                remboursement[invoice.id] = montant_verse[invoice.id] - dernier_versement[invoice.id]

        for invoice in invoices_no_move:
            untaxed_amount[invoice.id] = sum(
                (line.amount for line in invoice.lines
                    if line.type == 'line'), Decimal(0))
            total_amount[invoice.id] = (
                untaxed_amount[invoice.id] + tax_amount[invoice.id])
            
            if invoice.health_service != None:
                if invoice.health_service.insurance_plan != None:
                    montant_patient[invoice.id] = total_amount[invoice.id]
            else :
                montant_patient[invoice.id] = total_amount[invoice.id]

            if invoice.payment_lines :
                next_lines_moves = 1
                montant_verse[invoice.id] = 0
                remboursement[invoice.id] = 0
                i = 0
                while next_lines_moves != 0 :
                    next_lines_moves = MoveLine.search([('id', '=', invoice.payment_lines[len(invoice.payment_lines) -1].id+i)], limit=1)
                    if next_lines_moves and next_lines_moves[0].credit != 0 :
                        next_lines_moves = next_lines_moves[0].credit
                    else :
                        montant_verse[invoice.id] = next_lines_moves = MoveLine.search([('id', '=', invoice.payment_lines[len(invoice.payment_lines) -1].id+i)], limit=1)[0].debit
                        next_lines_moves = 0
                    i = i+1
                dernier_versement[invoice.id] = invoice.payment_lines[len(invoice.payment_lines) - 1].credit  
                remboursement[invoice.id] = montant_verse[invoice.id] - dernier_versement[invoice.id]
        
        if invoice.montant_assurance != None :
            total_amount2[invoice.id] = total_amount[invoice.id] + invoice.montant_assurance
            for invoice in invoices :
                record = invoice
                montant_f = invoice.montant_recu(record)[-1]
                if invoice.montant_assurance :
                    if invoice.montant_assurance > montant_f :
                        total_amount2[invoice.id] = montant_f
                    # with Transaction().new_transaction():
                    #     invoice.montant_assurance = montant_f
                    #     invoice.save()
            # <record.format_nombre(record.montant_recu(record)[-1])>
        else : 
            total_amount2[invoice.id] = total_amount[invoice.id]

        if invoice.health_service != None:
                if invoice.health_service.insurance_plan != None:
                    if invoice.health_service.insurance_plan.z_couverture == 100 and invoice.health_service.insurance_plan.plafond == None :
                        total_amount2[invoice.id] = invoice.montant_assurance


        result = {
            'untaxed_amount': untaxed_amount,
            'tax_amount': tax_amount,
            'total_amount': total_amount,
            'total_amount2': total_amount2,
            'montant_patient' : montant_patient,
            'dernier_versement' : dernier_versement,
            'montant_verse' : montant_verse,
            'remboursement' : remboursement,
            }
        for key in list(result.keys()):
            if key not in names:
                del result[key]
        return result

class InvoiceLine(metaclass=PoolMeta):
    __name__ = 'account.invoice.line'
    
    agent2 = fields.Many2One('commission.agent', 'Agent de Réalisation', select=True)

    @property
    def agent_plans_used(self):
        "List of agent, plan tuple"
        used = []
        if self.invoice.agent:
            used.append((self.invoice.agent, self.invoice.agent.plan))
        if self.principal:
            used.append((self.principal, self.principal.plan))
        if self.agent2:
            if self.agent2.plan2:
                used.append((self.agent2, self.agent2.plan2))
        return used
    

    def montant_produit(self):
        # Record corespond au recu
        # Format de la liste [prix1, prix2, prix3, prix4, prix5, total]

        sale_price_list = None

        if hasattr(self.invoice, 'tarifaire'):
            sale_price_list = self.invoice.tarifaire

        if  sale_price_list == None and hasattr(self.invoice.party, 'sale_price_list'):
            sale_price_list = self.invoice.party.sale_price_list

        unit_price = Decimal(0)
        if sale_price_list : 
            unit_price = sale_price_list.compute(
                            self.invoice.party,
                            self.product, self.product.list_price,
                            self.quantity, self.product.default_uom)
        
        return unit_price
    
    def get_commissions(self):
        pool = Pool()
        Commission = pool.get('commission')
        Currency = pool.get('currency.currency')
        Date = pool.get('ir.date')

        if self.type != 'line':
            return []

        today = Date.today()
        commissions = []
        for agent, plan in self.agent_plans_used:
            if not plan:
                continue
            with Transaction().set_context(date=self.invoice.currency_date):
                amount2 = float(self.montant_produit())*self.quantity
                amount = Currency.compute(self.invoice.currency,
                    amount2, agent.currency, round=False)
            amount = self._get_commission_amount(Decimal(amount), plan)
            if amount:
                amount = round_price(amount)
            if not amount:
                continue

            commission = Commission()
            commission.origin = self
            if plan.commission_method == 'posting':
                commission.date = self.invoice.invoice_date or today
            elif (plan.commission_method == 'payment'
                    and self.invoice.state == 'paid'):
                commission.date = self.invoice.reconciled or today
            commission.agent = agent
            commission.product = plan.commission_product
            commission.amount = amount
            commissions.append(commission)
        return commissions

    def _get_commission_amount(self, amount, plan, pattern=None):
        return plan.compute(amount, self.product, pattern=pattern)


class ImagingTestRequest(metaclass=PoolMeta):
    'Imaging Test Request'
    __name__ = 'gnuhealth.imaging.test.request'

    @staticmethod
    def default_doctor(self):
        return self.service.requestor

class PatientLabTestRequest(metaclass=PoolMeta):
    'Lab Order'
    __name__ = 'gnuhealth.patient.lab.test'

    @staticmethod
    def default_doctor_id(self):
        return self.service.requestor


class Party(metaclass=PoolMeta):
    __name__ = 'party.party'

    signature2 = fields.Binary("Picture of Signature")
    numero_carte = fields.Char("Numéro de Carte")
    clef = fields.Char('Clef') 


class ImagingTestResult(metaclass=PoolMeta):
    'Imaging Test Result'
    __name__ = 'gnuhealth.imaging.test.result'

    @staticmethod
    def prescriptor_name(id):

        pool = Pool()
        Result = pool.get('gnuhealth.imaging.test.request')
        Results = Result.search([('request', '=', id)], limit=1)
        return Results[0].service.requestor.name.name+" "+Results[0].service.requestor.name.lastname


class Agent(metaclass=PoolMeta):
    'Commission Agent'
    __name__ = 'commission.agent'

    plan2 = fields.Many2One('commission.plan', "Plan Réalisations",
        help="The plan used to calculate the commission for realisator.")
