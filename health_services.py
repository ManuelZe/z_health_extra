##############################################################################
#
#    GNU Health: The Free Health and Hospital Information System
#    Copyright (C) 2008-2022 Luis Falcon <lfalcon@gnusolidario.org>
#    Copyright (C) 2011-2022 GNU Solidario <health@gnusolidario.org>
#
#    Copyright (C) 2011  Adrián Bernardi, Mario Puntin (health_invoice)
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
from trytond.model import ModelView, ModelSQL, fields, Unique
from trytond.transaction import Transaction
from trytond.pyson import Eval, Equal
from trytond.pool import Pool
from trytond.i18n import gettext
from trytond.modules.health.core import get_institution
from trytond.pool import PoolMeta
from decimal import Decimal


class HealthService(metaclass=PoolMeta):
    'Health Service'
    __name__ = 'gnuhealth.health_service'

    requestor = fields.Many2One(
        'gnuhealth.healthprofessional', 'Prescripteur',
        help="Médécin prescripteur", select=True, required=True)
    agent = fields.Many2One('commission.agent', 'Agent de Commission',select=True, required=True)
    z_remise2 = fields.Numeric("Remise", digits=(3, 2), help="La Remise à appliquer sur la facture", required=False)
    tarifaire = fields.Many2One('product.price_list','Tarifaire', required=False, select=True)

    @fields.depends('z_remise2')
    def on_change_with_z_remise2(self):
        if self.z_remise2 :
            return Decimal(10)
    
    @fields.depends('patient')
    def on_change_with_tarifaire(self):
        tarifaire = self.patient.name.sale_price_list

        return tarifaire