# -*- coding: utf-8 -*-
##############################################################################
#
#    GNU Health: The Free Health and Hospital Information System
#    Copyright (C) 2008-2022 Luis Falcon <lfalcon@gnusolidario.org>
#    Copyright (C) 2011-2022 GNU Solidario <health@gnusolidario.org>
#
#
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
import datetime
from trytond.model import ModelView
from trytond.wizard import Wizard, StateTransition, StateView, Button
from trytond.transaction import Transaction
from trytond.pool import Pool


class GenerateResultsCommissionInit(ModelView):
    'Generate Result Bordearux Init'
    __name__ = 'results.com.init'


class GenerateResultsCommission(Wizard):
    'Generate Results Bord'
    __name__ = 'results.bord.create'

    start = StateView('results.com.init',
        'z_Bordereau_De_Transmission.view_generate_results_bordereaux', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Generate Commission', 'generate_results_com', 'tryton-ok',
                True),
            ])
    generate_results_com = StateTransition()

    def transition_generate_results_com(cls):
            # Create commission only the first time the invoice is posted
            Invoice = Pool().get('account.invoice')
            paid_invoices = Invoice.search([('state', '=', 'paid')])
            to_commission = [i for i in paid_invoices
                if i.state in ['posted', 'paid']]
            # super()._post(invoices)
            cls.create_commissions(to_commission)

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