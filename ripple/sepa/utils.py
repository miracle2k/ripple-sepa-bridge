from functools import wraps
import string
from datetime import timedelta, datetime
import stdnum.iban
from stdnum.exceptions import ValidationError
import base64
from flask import make_response


# 246 official ISO 3166-1-alpha-2 codes
# From https://github.com/gatoni/iso-country-codes/blob/master/iso_country_codes.py
COUNTRIES = {
    "AF":"AFGHANISTAN",
    "AX":"ALAND ISLANDS",
    "AL":"ALBANIA",
    "DZ":"ALGERIA",
    "AS":"AMERICAN SAMOA",
    "AD":"ANDORRA",
    "AO":"ANGOLA",
    "AI":"ANGUILLA",
    "AQ":"ANTARCTICA",
    "AG":"ANTIGUA AND BARBUDA",
    "AR":"ARGENTINA",
    "AM":"ARMENIA",
    "AW":"ARUBA",
    "AU":"AUSTRALIA",
    "AT":"AUSTRIA",
    "AZ":"AZERBAIJAN",
    "BS":"BAHAMAS",
    "BH":"BAHRAIN",
    "BD":"BANGLADESH",
    "BB":"BARBADOS",
    "BY":"BELARUS",
    "BE":"BELGIUM",
    "BZ":"BELIZE",
    "BJ":"BENIN",
    "BM":"BERMUDA",
    "BT":"BHUTAN",
    "BO":"BOLIVIA, PLURINATIONAL STATE OF",
    "BA":"BOSNIA AND HERZEGOVINA",
    "BW":"BOTSWANA",
    "BV":"BOUVET ISLAND",
    "BR":"BRAZIL",
    "IO":"BRITISH INDIAN OCEAN TERRITORY",
    "BN":"BRUNEI DARUSSALAM",
    "BG":"BULGARIA",
    "BF":"BURKINA FASO",
    "BI":"BURUNDI",
    "KH":"CAMBODIA",
    "CM":"CAMEROON",
    "CA":"CANADA",
    "CV":"CAPE VERDE",
    "KY":"CAYMAN ISLANDS",
    "CF":"CENTRAL AFRICAN REPUBLIC",
    "TD":"CHAD",
    "CL":"CHILE",
    "CN":"CHINA",
    "CX":"CHRISTMAS ISLAND",
    "CC":"COCOS (KEELING) ISLANDS",
    "CO":"COLOMBIA",
    "KM":"COMOROS",
    "CG":"CONGO",
    "CD":"CONGO, THE DEMOCRATIC REPUBLIC OF THE",
    "CK":"COOK ISLANDS",
    "CR":"COSTA RICA",
    "CI":"COTE D'IVOIRE",
    "HR":"CROATIA",
    "CU":"CUBA",
    "CY":"CYPRUS",
    "CZ":"CZECH REPUBLIC",
    "DK":"DENMARK",
    "DJ":"DJIBOUTI",
    "DM":"DOMINICA",
    "DO":"DOMINICAN REPUBLIC",
    "EC":"ECUADOR",
    "EG":"EGYPT",
    "SV":"EL SALVADOR",
    "GQ":"EQUATORIAL GUINEA",
    "ER":"ERITREA",
    "EE":"ESTONIA",
    "ET":"ETHIOPIA",
    "FK":"FALKLAND ISLANDS (MALVINAS)",
    "FO":"FAROE ISLANDS",
    "FJ":"FIJI",
    "FI":"FINLAND",
    "FR":"FRANCE",
    "GF":"FRENCH GUIANA",
    "PF":"FRENCH POLYNESIA",
    "TF":"FRENCH SOUTHERN TERRITORIES",
    "GA":"GABON",
    "GM":"GAMBIA",
    "GE":"GEORGIA",
    "DE":"GERMANY",
    "GH":"GHANA",
    "GI":"GIBRALTAR",
    "GR":"GREECE",
    "GL":"GREENLAND",
    "GD":"GRENADA",
    "GP":"GUADELOUPE",
    "GU":"GUAM",
    "GT":"GUATEMALA",
    "GG":"GUERNSEY",
    "GN":"GUINEA",
    "GW":"GUINEA-BISSAU",
    "GY":"GUYANA",
    "HT":"HAITI",
    "HM":"HEARD ISLAND AND MCDONALD ISLANDS",
    "VA":"HOLY SEE (VATICAN CITY STATE)",
    "HN":"HONDURAS",
    "HK":"HONG KONG",
    "HU":"HUNGARY",
    "IS":"ICELAND",
    "IN":"INDIA",
    "ID":"INDONESIA",
    "IR":"IRAN, ISLAMIC REPUBLIC OF",
    "IQ":"IRAQ",
    "IE":"IRELAND",
    "IM":"ISLE OF MAN",
    "IL":"ISRAEL",
    "IT":"ITALY",
    "JM":"JAMAICA",
    "JP":"JAPAN",
    "JE":"JERSEY",
    "JO":"JORDAN",
    "KZ":"KAZAKHSTAN",
    "KE":"KENYA",
    "KI":"KIRIBATI",
    "KP":"KOREA, DEMOCRATIC PEOPLE'S REPUBLIC OF",
    "KR":"KOREA, REPUBLIC OF",
    "KW":"KUWAIT",
    "KG":"KYRGYZSTAN",
    "LA":"LAO PEOPLE'S DEMOCRATIC REPUBLIC",
    "LV":"LATVIA",
    "LB":"LEBANON",
    "LS":"LESOTHO",
    "LR":"LIBERIA",
    "LY":"LIBYAN ARAB JAMAHIRIYA",
    "LI":"LIECHTENSTEIN",
    "LT":"LITHUANIA",
    "LU":"LUXEMBOURG",
    "MO":"MACAO",
    "MK":"MACEDONIA, THE FORMER YUGOSLAV REPUBLIC OF",
    "MG":"MADAGASCAR",
    "MW":"MALAWI",
    "MY":"MALAYSIA",
    "MV":"MALDIVES",
    "ML":"MALI",
    "MT":"MALTA",
    "MH":"MARSHALL ISLANDS",
    "MQ":"MARTINIQUE",
    "MR":"MAURITANIA",
    "MU":"MAURITIUS",
    "YT":"MAYOTTE",
    "MX":"MEXICO",
    "FM":"MICRONESIA, FEDERATED STATES OF",
    "MD":"MOLDOVA, REPUBLIC OF",
    "MC":"MONACO",
    "MN":"MONGOLIA",
    "ME":"MONTENEGRO",
    "MS":"MONTSERRAT",
    "MA":"MOROCCO",
    "MZ":"MOZAMBIQUE",
    "MM":"MYANMAR",
    "NA":"NAMIBIA",
    "NR":"NAURU",
    "NP":"NEPAL",
    "NL":"NETHERLANDS",
    "AN":"NETHERLANDS ANTILLES",
    "NC":"NEW CALEDONIA",
    "NZ":"NEW ZEALAND",
    "NI":"NICARAGUA",
    "NE":"NIGER",
    "NG":"NIGERIA",
    "NU":"NIUE",
    "NF":"NORFOLK ISLAND",
    "MP":"NORTHERN MARIANA ISLANDS",
    "NO":"NORWAY",
    "OM":"OMAN",
    "PK":"PAKISTAN",
    "PW":"PALAU",
    "PS":"PALESTINIAN TERRITORY, OCCUPIED",
    "PA":"PANAMA",
    "PG":"PAPUA NEW GUINEA",
    "PY":"PARAGUAY",
    "PE":"PERU",
    "PH":"PHILIPPINES",
    "PN":"PITCAIRN",
    "PL":"POLAND",
    "PT":"PORTUGAL",
    "PR":"PUERTO RICO",
    "QA":"QATAR",
    "RE":"REUNION",
    "RO":"ROMANIA",
    "RU":"RUSSIAN FEDERATION",
    "RW":"RWANDA",
    "BL":"SAINT BARTHELEMY",
    "SH":"SAINT HELENA, ASCENSION AND TRISTAN DA CUNHA",
    "KN":"SAINT KITTS AND NEVIS",
    "LC":"SAINT LUCIA",
    "MF":"SAINT MARTIN",
    "PM":"SAINT PIERRE AND MIQUELON",
    "VC":"SAINT VINCENT AND THE GRENADINES",
    "WS":"SAMOA",
    "SM":"SAN MARINO",
    "ST":"SAO TOME AND PRINCIPE",
    "SA":"SAUDI ARABIA",
    "SN":"SENEGAL",
    "RS":"SERBIA",
    "SC":"SEYCHELLES",
    "SL":"SIERRA LEONE",
    "SG":"SINGAPORE",
    "SK":"SLOVAKIA",
    "SI":"SLOVENIA",
    "SB":"SOLOMON ISLANDS",
    "SO":"SOMALIA",
    "ZA":"SOUTH AFRICA",
    "GS":"SOUTH GEORGIA AND THE SOUTH SANDWICH ISLANDS",
    "ES":"SPAIN",
    "LK":"SRI LANKA",
    "SD":"SUDAN",
    "SR":"SURINAME",
    "SJ":"SVALBARD AND JAN MAYEN",
    "SZ":"SWAZILAND",
    "SE":"SWEDEN",
    "CH":"SWITZERLAND",
    "SY":"SYRIAN ARAB REPUBLIC",
    "TW":"TAIWAN, PROVINCE OF CHINA",
    "TJ":"TAJIKISTAN",
    "TZ":"TANZANIA, UNITED REPUBLIC OF",
    "TH":"THAILAND",
    "TL":"TIMOR-LESTE",
    "TG":"TOGO",
    "TK":"TOKELAU",
    "TO":"TONGA",
    "TT":"TRINIDAD AND TOBAGO",
    "TN":"TUNISIA",
    "TR":"TURKEY",
    "TM":"TURKMENISTAN",
    "TC":"TURKS AND CAICOS ISLANDS",
    "TV":"TUVALU",
    "UG":"UGANDA",
    "UA":"UKRAINE",
    "AE":"UNITED ARAB EMIRATES",
    "GB":"UNITED KINGDOM",
    "US":"UNITED STATES",
    "UM":"UNITED STATES MINOR OUTLYING ISLANDS",
    "UY":"URUGUAY",
    "UZ":"UZBEKISTAN",
    "VU":"VANUATU",
    "VE":"VENEZUELA, BOLIVARIAN REPUBLIC OF",
    "VN":"VIET NAM",
    "VG":"VIRGIN ISLANDS, BRITISH",
    "VI":"VIRGIN ISLANDS, U.S.",
    "WF":"WALLIS AND FUTUNA",
    "EH":"WESTERN SAHARA",
    "YE":"YEMEN",
    "ZM":"ZAMBIA",
    "ZW ":"ZIMBABWE"
}


def validate_swift_bic(value):
    """ Validation for ISO 9362:2009 (SWIFT-BIC).

    From:
    https://github.com/SmileyChris/django-countries/blob/master/django_countries/ioc_data.py
    """

    # Length is 8 or 11.
    swift_bic_length = len(value)
    if swift_bic_length != 8 and swift_bic_length != 11:
        raise ValueError('A SWIFT-BIC is either 8 or 11 characters long.')

    # First 4 letters are A - Z.
    institution_code = value[:4]
    for x in institution_code:
        if x not in string.ascii_uppercase:
            raise ValueError('{0} is not a valid SWIFT-BIC Institution Code.'.format(institution_code))

    # Letters 5 and 6 consist of an ISO 3166-1 alpha-2 country code.
    country_code = value[4:6]
    if country_code not in COUNTRIES:
        raise ValueError('{0} is not a valid SWIFT-BIC Country Code.').format(country_code)

    return value


def add_response_headers(headers={}):
    """This decorator adds the headers passed in to the response"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            resp = make_response(f(*args, **kwargs))
            h = resp.headers
            for header, value in headers.items():
                h[header] = value
            return resp
        return decorated_function
    return decorator


def parse_sepa_destination(s):
    """Parse a string into a dict of SEPA information. The format is::

        recipient+name/IBAN/BIC/foo+bar

    The text is optional. Since this format is only intended to provide
    a way to pre-fill the SEPA form, as opposed to entered by a human,
    we can afford to be pretty rigerous about it.

    Additional, it is supported that the string is base64 encoded
    for safe keeping of spaces, which are not supported by the
    Ripple client in destinations.
    """
    try:
        s = base64.b64decode(s).decode('utf-8')
    except:
        pass

    enable_spaces = lambda s: s.replace('+', ' ')
    parts = s.split('/', 4)
    if len(parts) not in (3, 4):
        raise ValueError('Expecting either 3 or 4 parts separated by /')
    parts = [enable_spaces(p) for p in parts]
    recipient_name, iban, bic = parts[0], parts[1], parts[2]
    text = parts[3] if len(parts) == 4 else ''
    return {
        'name': recipient_name,
        'text': text,
        'iban': iban,
        'bic': bic
    }


def validate_sepa(sepa):
    """Will validate the dict of SEPA data (as returned by
    :meth:`parse_sepa_destination`) and raise ValueErrors if there
    are problems.
    """
    # Validate IBAN
    if not 'iban' in sepa:
        raise ValueError('An IBAN needs to be provided')
    try:
        stdnum.iban.validate(sepa['iban'])
    except ValidationError:
        raise ValueError('%s is not a valid IBAN' % sepa['iban'])

    # Validate BIC
    if not 'bic' in sepa:
        raise ValueError('An BIC needs to be provided')
    try:
        validate_swift_bic(sepa['bic'])
    except ValidationError:
        raise ValueError('%s is not a valid BIC' % sepa['bic'])

    # Make sure there is a recipient name
    if not sepa['name']:
        raise ValueError('The name of the recipient needs to be provided')

    # Make sure the text is not too long
    if sepa['text']:
        # Note: SEPA limit is 140, but leave some room for any custom
        # text we might want to insert.
        if len(sepa['text']) > 130:
            raise ValueError('Text may not be longer than 130 characters')


def timesince(d, now=None, reversed=False):
    """From Django.
    """
    ugettext = lambda s: s
    ungettext = lambda s, p, n: s if n == 1 else p
    chunks = (
      (60 * 60 * 24 * 365, lambda n: ungettext('year', 'years', n)),
      (60 * 60 * 24 * 30, lambda n: ungettext('month', 'months', n)),
      (60 * 60 * 24 * 7, lambda n : ungettext('week', 'weeks', n)),
      (60 * 60 * 24, lambda n : ungettext('day', 'days', n)),
      (60 * 60, lambda n: ungettext('hour', 'hours', n)),
      (60, lambda n: ungettext('minute', 'minutes', n))
    )
    # Convert datetime.date to datetime.datetime for comparison.
    if not isinstance(d, datetime):
        d = datetime(d.year, d.month, d.day)
    if now and not isinstance(now, datetime):
        now = datetime(now.year, now.month, now.day)

    if not now:
        if d.tzinfo:
            #now = datetime.now(to_local_timezone(d))
            now = datetime.now()
        else:
            now = datetime.utcnow()

    # ignore microsecond part of 'd' since we removed it from 'now'
    delta = now - (d - timedelta(0, 0, d.microsecond))
    since = delta.days * 24 * 60 * 60 + delta.seconds
    if since <= 0:
        # d is in the future compared to now, stop processing.
        return u'0 ' + ugettext('minutes')
    for i, (seconds, name) in enumerate(chunks):
        count = since // seconds
        if count != 0:
            break
    s = ugettext('%(number)d %(type)s') % {'number': count, 'type': name(count)}
    if i + 1 < len(chunks):
        # Now get the second item
        seconds2, name2 = chunks[i + 1]
        count2 = (since - (seconds * count)) // seconds2
        if count2 != 0:
            s += ugettext(', %(number)d %(type)s') % {'number': count2, 'type': name2(count2)}
    return s
