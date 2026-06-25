# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
from decimal import Decimal
from functools import total_ordering

from simpleeval import simple_eval

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, MatchMixin, ModelSQL, ModelView, fields,
    sequence_ordered)
from trytond.pool import Pool


@total_ordering
class Null(Decimal):
    def __eq__(self, other):
        if isinstance(other, Null) or other is None:
            return True
        return False

    def __lt__(self, other):
        return 0 < other


class PriceList(DeactivableMixin, ModelSQL, ModelView):
    'Price List'
    __name__ = 'product.price_list'

    def get_context_formula(self, product, quantity, uom, pattern=None):

        print(f"self.price --------- {self.price}")
        if product:
            cost_price = product.get_multivalue('cost_price') or Decimal('0')
            list_price = product.list_price_used
        else:
            cost_price = Decimal('0')
            list_price = Null()
        if self.price == 'list_price':
            unit_price = list_price
        elif self.price == 'cost_price':
            unit_price = cost_price
        else:
            unit_price = Null()
        return {
            'names': {
                'unit_price': unit_price if unit_price is not None else Null(),
                'cost_price': cost_price if cost_price is not None else Null(),
                'list_price': list_price if list_price is not None else Null(),
                },
            }

    def get_uom(self, product):
        return product.default_uom
    

    def compute(self, product, quantity, uom, pattern=None):
        Uom = Pool().get('product.uom')

        def parents(categories):
            for category in categories:
                while category:
                    yield category
                    category = category.parent

        if pattern is None:
            pattern = {}

        pattern = pattern.copy()
        if product:
            pattern['categories'] = [
                c.id for c in parents(product.categories_all)]
            pattern['product'] = product.id
        pattern['quantity'] = Uom.compute_qty(uom, quantity,
            self.get_uom(product), round=False) if product else quantity

        context = self.get_context_formula(
            product, quantity, uom, pattern=pattern)
        
        print(f"-----Le context {context} et le produit en questio  ------ {product} et la quantité est : {quantity} et l'unité de mesure est : {uom}")
        for line in self.lines:
            if line.match(pattern):
                unit_price = line.get_unit_price(**context)
                print(f"-----Le prix unitaire est : {unit_price} pour le produit : {product} et la quantité est : {quantity} et l'unité de mesure est : {uom}")
                if isinstance(unit_price, Null):
                    unit_price = None
                return unit_price