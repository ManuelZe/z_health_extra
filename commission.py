# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
from collections import defaultdict
from trytond.pool import PoolMeta, Pool
from trytond.model import ModelView, Workflow, fields
from sql.aggregate import Sum
from trytond.pyson import Eval, If, Bool
from trytond.transaction import Transaction
from trytond.tools import grouped_slice
from decimal import Decimal

from trytond.modules.product import round_price


class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'
    

    # @classmethod
    # def _post(cls, invoices):
    #     # Create commission only the first time the invoice is posted
    #     to_commission = [i for i in invoices
    #         if i.state not in ['posted', 'paid']]
    #     super()._post(invoices)
    #     cls.create_commissions(to_commission)
    
    @classmethod
    def _post(cls, invoices):
        # Create commission only the first time the invoice is posted
        to_commission = [i for i in invoices
            if i.state not in ['posted', 'paid']]
        super()._post(invoices)
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
                print("ce qu'il faut davoir -------- ", commissions)
                if commissions:
                    all_commissions.extend(commissions)

        Commission.save(all_commissions)
        return all_commissions


class InvoiceLine(metaclass=PoolMeta):
    __name__ = 'account.invoice.line'
    
    def montant_produit(self):
        # Record corespond au recu
        # Format de la liste [prix1, prix2, prix3, prix4, prix5, total]

        sale_price_list = None
        if hasattr(self.invoice.party, 'sale_price_list'):
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
                amount2 = self.montant_produit()
                amount = Currency.compute(self.invoice.currency,
                    amount2, agent.currency, round=False)
            
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
