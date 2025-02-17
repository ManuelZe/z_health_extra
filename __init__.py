# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

from trytond.pool import Pool
from .import health_services
from . import health
from .wizard import wizard_health_insurance

__all__ = ['register']


def register():
    Pool.register(
        health_services.HealthService,
        health.Insurance,
        health.Invoice,
        health.Lab,
        health.LabTestType,
        health.TestType,
        health.PayInvoiceStart,
        health.Commission,
        health.ImagingTestRequest,
        health.PatientLabTestRequest,
        health.Party,
        health.ImagingTestResult,
        module='z_health_extra', type_='model')
    Pool.register(
        health.PayInvoice,
        wizard_health_insurance.CreateServiceInvoice,
        module='z_health_extra', type_='wizard')
    Pool.register(
        module='z_health_extra', type_='report')
