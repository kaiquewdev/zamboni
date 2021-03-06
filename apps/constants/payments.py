from tower import ugettext_lazy as _

# Paypal is an awful place that doesn't understand locales.  Instead they have
# country codes.  This maps our locales to their codes.
PAYPAL_COUNTRYMAP = {
    'af': 'ZA', 'ar': 'EG', 'ca': 'ES', 'cs': 'CZ', 'cy': 'GB', 'da': 'DK',
    'de': 'DE', 'de-AT': 'AT', 'de-CH': 'CH', 'el': 'GR', 'en-GB': 'GB',
    'eu': 'BS', 'fa': 'IR', 'fi': 'FI', 'fr': 'FR', 'he': 'IL', 'hu': 'HU',
    'id': 'ID', 'it': 'IT', 'ja': 'JP', 'ko': 'KR', 'mn': 'MN', 'nl': 'NL',
    'pl': 'PL', 'ro': 'RO', 'ru': 'RU', 'sk': 'SK', 'sl': 'SI', 'sq': 'AL',
    'sr': 'CS', 'tr': 'TR', 'uk': 'UA', 'vi': 'VI',
}

# Source, PayPal docs, PP_AdaptivePayments.PDF
PAYPAL_CURRENCIES = {
    'AUD': _('Australian Dollar'),
    'BRL': _('Brazilian Real'),
    'CAD': _('Canadian Dollar'),
    'CZK': _('Czech Koruna'),
    'DKK': _('Danish Krone'),
    'EUR': _('Euro'),
    'HKD': _('Hong Kong Dollar'),
    'HUF': _('Hungarian Forint'),
    'ILS': _('Israeli New Sheqel'),
    'JPY': _('Japanese Yen'),
    'MYR': _('Malaysian Ringgit'),
    'MXN': _('Mexican Peso'),
    'NOK': _('Norwegian Krone'),
    'NZD': _('New Zealand Dollar'),
    'PHP': _('Philippine Peso'),
    'PLN': _('Polish Zloty'),
    'GBP': _('Pound Sterling'),
    'SGD': _('Singapore Dollar'),
    'SEK': _('Swedish Krona'),
    'CHF': _('Swiss Franc'),
    'TWD': _('Taiwan New Dollar'),
    'THB': _('Thai Baht'),
    'USD': _('U.S. Dollar'),
}

OTHER_CURRENCIES = PAYPAL_CURRENCIES.copy()
del OTHER_CURRENCIES['USD']

# Need to find a more complete list for this. This is just a sample.
LOCALE_CURRENCY = {
    'en_US': 'USD',
    'en_CA': 'CAD',
    'it': 'EUR',
    'fr': 'EUR',
    'pt_BR': 'BRL',
}

CURRENCY_DEFAULT = 'USD'

CONTRIB_VOLUNTARY = 0
CONTRIB_PURCHASE = 1
CONTRIB_REFUND = 2
CONTRIB_CHARGEBACK = 3
# We've started a transaction and we need to wait to see what
# paypal will return.
CONTRIB_PENDING = 4
CONTRIB_OTHER = 99

CONTRIB_TYPES = {
    CONTRIB_VOLUNTARY: _('Voluntary'),
    CONTRIB_PURCHASE: _('Purchase'),
    CONTRIB_REFUND: _('Refund'),
    CONTRIB_CHARGEBACK: _('Chargeback'),
    CONTRIB_OTHER: _('Other'),
}

CONTRIB_NOT_PENDING = (CONTRIB_VOLUNTARY, CONTRIB_PURCHASE,
                       CONTRIB_CHARGEBACK, CONTRIB_OTHER)
CONTRIB_TYPE_DEFAULT = CONTRIB_VOLUNTARY

INAPP_STATUS_ACTIVE = 0
INAPP_STATUS_INACTIVE = 1
INAPP_STATUS_REVOKED = 2

INAPP_STATUS_CHOICES = (
    (INAPP_STATUS_ACTIVE, _('Active')),
    (INAPP_STATUS_INACTIVE, _('Inactive')),
    (INAPP_STATUS_REVOKED, _('Revoked'))
)

PAYPAL_PERSONAL = {
    'first': 'http://axschema.org/namePerson/first',
    'last': 'http://axschema.org/namePerson/last',
    'email': 'http://axschema.org/contact/email',
    'fullname': 'http://schema.openid.net/contact/fullname',
    'company': 'http://openid.net/schema/company/name',
    'country': 'http://axschema.org/contact/country/home',
    'payerID': 'https://www.paypal.com/webapps/auth/schema/payerID',
    'birthDate': 'http://axschema.org/birthDate',
    'home': 'http://axschema.org/contact/postalCode/home',
    'street1': 'http://schema.openid.net/contact/street1',
    'street2': 'http://schema.openid.net/contact/street2',
    'city': 'http://axschema.org/contact/city/home',
    'state': 'http://axschema.org/contact/state/home',
    'phone': 'http://axschema.org/contact/phone/default'
}
PAYPAL_PERSONAL_LOOKUP = dict([(v, k) for k, v
                                      in PAYPAL_PERSONAL.iteritems()])
