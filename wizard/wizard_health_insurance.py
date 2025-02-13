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
from decimal import ROUND_HALF_UP
import decimal
from trytond.wizard import Wizard
from trytond.transaction import Transaction
from trytond.pool import PoolMeta
from trytond.pool import Pool
from trytond.i18n import gettext
from decimal import Decimal
from trytond.modules.product import round_price
from ..exceptions import (
    ServiceInvoiced, NoInvoiceAddress, NoPaymentTerm, NoAccountReceivable)

__all__ = ['CreateServiceInvoice']


class CreateServiceInvoice(metaclass=PoolMeta):
    __name__ = 'gnuhealth.service.invoice.create'

#  name: CreateServiceInvoice.discount_policy
#  @param : Insurance, service line product
#  @return : Policy applied to that service line

    def transition_create_service_invoice(self):
        pool = Pool()
        HealthService = pool.get('gnuhealth.health_service')
        Invoice = pool.get('account.invoice')
        Party = pool.get('party.party')
        Journal = pool.get('account.journal')
        AcctConfig = pool.get('account.configuration')
        acct_config = AcctConfig(1)

        currency_id = Transaction().context.get('currency')

        services = HealthService.browse(Transaction().context.get(
            'active_ids'))
        invoices = []

        # Invoice Header
        for service in services:
            if service.state == 'invoiced':
                raise ServiceInvoiced(
                    gettext('health_insurance.msg_service_invoiced')
                    )
            if service.invoice_to:
                party = service.invoice_to
            else:
                party = service.patient.name
            invoice_data = {}
            invoice_data['description'] = service.desc
            invoice_data['party'] = party.id
            invoice_data['type'] = 'out'
            invoice_data['invoice_date'] = datetime.date.today()
            invoice_data['company'] = service.company.id
            invoice_data['agent'] = service.agent.id

            """ Look for the AR account in the following order:
                * Party
                * Default AR in accounting config
                * Raise an error if there is no AR account
            """
            if (party.account_receivable):
                invoice_data['account'] = party.account_receivable.id
            elif (acct_config.default_account_receivable):
                invoice_data['account'] = \
                    acct_config.default_account_receivable.id
            else:
                raise NoAccountReceivable(
                    gettext('health_insurance.msg_no_account_receivable'))

            ctx = {}
            sale_price_list = None
            if hasattr(party, 'sale_price_list'):
                sale_price_list = party.sale_price_list

            if sale_price_list:
                ctx['price_list'] = sale_price_list.id
                ctx['sale_date'] = datetime.date.today()
                ctx['currency'] = currency_id
                ctx['customer'] = party.id

            journals = Journal.search([
                ('type', '=', 'revenue'),
                ], limit=1)

            if journals:
                journal, = journals
            else:
                journal = None

            invoice_data['journal'] = journal.id

            party_address = Party.address_get(party, type='invoice')
            if not party_address:
                raise NoInvoiceAddress(
                    gettext('health_insurance.msg_no_invoice_address')
                    )

            invoice_data['invoice_address'] = party_address.id
            invoice_data['reference'] = service.name

            """ Look for the payment term in the following order:
                * Party
                * Default payment term in accounting config
                * Raise an error if there is no payment term
            """
            if (party.customer_payment_term):
                invoice_data['payment_term'] = party.customer_payment_term.id
            elif (acct_config.default_customer_payment_term):
                invoice_data['payment_term'] = \
                    acct_config.default_customer_payment_term.id
            else:
                raise NoPaymentTerm(
                    gettext('health_insurance.msg_no_payment_term')
                    )

            # Invoice Lines
            seq = 0
            invoice_lines = []
            plafond = Decimal(0)
            if service.insurance_plan and service.insurance_plan.plafond :
                plafond = service.insurance_plan.plafond
            total_assurance = Decimal(0)
            for line in service.service_line:
                plafond2 = plafond
                seq = seq + 1
                account = line.product.template.account_revenue_used.id

                if sale_price_list:
                    with Transaction().set_context(ctx):
                        unit_price = sale_price_list.compute(
                            party,
                            line.product, line.product.list_price,
                            line.qty, line.product.default_uom)
                else:
                    unit_price = line.product.list_price
                
                unit_price2 = unit_price

                if line.to_invoice:
                    taxes = []
                    desc = line.desc

                    # Include taxes related to the product on the invoice line
                    for product_tax_line in line.product.customer_taxes_used:
                        taxes.append(product_tax_line.id)

                    # Check the Insurance policy for this service
                    if service.insurance_plan:
                        discount = self.discount_policy(
                            service.insurance_plan,
                            line.product)

                        amount = unit_price * line.qty


                        if discount :
                            print("regardons le plafond et le discount[]", plafond==Decimal(0), "-------------- ", discount['value'])
                            
                            if discount['value'] == 100.0:
                                print("cas 1 ------------------------")
                                if 'value' in list(discount.keys()):
                                    if discount['value']:
                                        if (discount['type'] == 'pct'):
                                            unit_price = Decimal('0.1')
                                            # Use price_decimal value from
                                            # system configuration to set
                                            # the number of decimals
                                            unit_price = round_price(unit_price)

                                            # Add remark on description discount
                                            str_disc = str(discount['value']) + '%'
                                            desc = line.desc + " (Assurance " + \
                                                str(str_disc) + ")"
                                            
                                            montant_ass = (unit_price2)*line.qty
                                                
                                        else:
                                            unit_price = discount['value']
                                            desc = f"{line.desc} (Assurance plan)"
                                            montant_ass = (unit_price2)*line.qty
                            
                                        montant_ass = montant_ass.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

                            else :
                                if 'value' in list(discount.keys()):
                                    if discount['value']:
                                        if (discount['type'] == 'pct'):
                                            unit_price *= decimal.Decimal(
                                                1 - discount['value']/100)
                                            # Use price_decimal value from
                                            # system configuration to set
                                            # the number of decimals
                                            unit_price = round_price(unit_price)

                                            # Add remark on description discount
                                            str_disc = str(discount['value']) + '%'
                                            desc = line.desc + " (Assurance " + \
                                                str(str_disc) + ")"

                                            montant_ass = (unit_price2 - unit_price)*line.qty                            
                                        else:
                                            unit_price = discount['value']
                                            desc = f"{line.desc} (Assurance plan)"
                                            montant_ass = (unit_price2)*line.qty

                            print("le plafond des plafond ------ ", plafond)

                        if service.insurance_plan.plafond != None and plafond == Decimal(0) :
                                print("Au cas où le plafond est 0", plafond)
                                montant_ass = service.insurance_plan.plafond 
                                if montant_ass :
                                    montant_ass = montant_ass.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                                unit_price = unit_price2
                                amount = unit_price * line.qty


                        if plafond != Decimal(0) :
                            print("Le dernier des cas")
                            montant_ass = service.insurance_plan.plafond
                            montant_ass = montant_ass.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                            if discount :
                                if discount['value'] :
                                    str_disc = str(discount['value']) + '%'
                                    desc = line.desc + " (Assurance " + \
                                                str(str_disc) + ")"
                            if Decimal(plafond) > Decimal(0) :
                                if Decimal(amount) < Decimal(plafond) :
                                    unit_price = Decimal(0)
                                    plafond -= Decimal(amount)
                                elif Decimal(amount) > Decimal(plafond):
                                    amount = amount - plafond
                                    if amount == Decimal(0) :
                                        unit_price = Decimal('0.1')
                                    else :
                                        unit_price = amount/line.qty
                                    plafond = Decimal(0)


                    elif service.z_remise2 :
                        remise = service.z_remise2
                        amount = unit_price * line.qty
                        if remise/100 != 1 :
                            amount *= decimal.Decimal(1 - remise / 100)

                            unit_price = amount/line.qty

                    invoice_lines.append(('create', [{
                            'origin': str(line),
                            'product': line.product.id,
                            'description': desc,
                            'quantity': line.qty,
                            'account': account,
                            'unit': line.product.default_uom.id,
                            'unit_price': unit_price,
                            'sequence': seq,
                            'taxes': [('add', taxes)],
                        }]))
                invoice_data['lines'] = invoice_lines

                if service.insurance_plan and service.insurance_plan.plafond :
                    total_assurance = service.insurance_plan.plafond
                elif service.insurance_plan :
                    total_assurance += montant_ass

            total_assurance = total_assurance.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            invoice_data['montant_assurance'] = total_assurance

            invoices.append(invoice_data)

        Invoice.update_taxes(Invoice.create(invoices))

        # Change to invoiced the status on the service document.
        HealthService.write(services, {'state': 'invoiced'})

        return 'end'