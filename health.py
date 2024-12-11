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
import ftplib
import platform
import os
import string
import random
import pytz

from dateutil.relativedelta import relativedelta
from datetime import datetime, timedelta, date
from urllib.parse import urlencode
from urllib.parse import urlunparse
from collections import OrderedDict
from io import BytesIO
from uuid import uuid4

from sql import Literal, Join

from trytond.model import (ModelView, ModelSingleton, ModelSQL,
                           MultiValueMixin, fields, Unique, tree)
from trytond.wizard import Wizard, StateAction, StateView, Button
from trytond.transaction import Transaction
from trytond.pyson import Eval, Not, Bool, PYSONEncoder, Equal, And, Or
from trytond.pool import Pool
from trytond.rpc import RPC
from trytond.i18n import gettext
from trytond.pool import PoolMeta


from .exceptions import (
    WrongDateofBirth, DateHealedBeforeDx, EndTreatmentDateBeforeStart,
    MedEndDateBeforeStart, NextDoseBeforeFirst, DrugPregnancySafetyCheck,
    EvaluationEndBeforeStart, MustBeAPerson, NoAssociatedHealthProfessional,
    DupOfficialName, FedAccountMismatch, BirthCertDateMismatch,
    CanNotModifyVaccination
    )

from .core import (get_institution, compute_age_from_dates,
                   get_health_professional)

# ftp = ftplib.FTP('172.16.145.184')
# ftp.login('iuc','iuc123456')
try:
    from PIL import Image
except ImportError:
    Image = None



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