# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
from collections import defaultdict
from trytond.pool import PoolMeta, Pool
from trytond.model import ModelView, Workflow, fields
from sql.aggregate import Sum
from trytond.pyson import Eval, If, Bool
from trytond.transaction import Transaction
from trytond.tools import grouped_slice
from trytond.report import Report

from trytond.modules.product import round_price

class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'

    def on_change_agent(self, name):
        try:
            return self.lines[0].origin.name.agent.id
        except:
            return None

