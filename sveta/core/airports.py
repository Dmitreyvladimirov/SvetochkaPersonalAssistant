"""IATA airport → IANA time zone for the flights that show up in this user's mail.
A flight leaves at the *airport's* local time; a calendar event created in the
user's zone would be hours off. Unknown code → None, the caller falls back to
the user's zone and says so on the card."""
AIRPORT_TZ = {
    # Israel
    "TLV": "Asia/Jerusalem", "ETM": "Asia/Jerusalem", "HFA": "Asia/Jerusalem",
    # Spain / Portugal
    "MAD": "Europe/Madrid", "BCN": "Europe/Madrid", "AGP": "Europe/Madrid", "PMI": "Europe/Madrid",
    "VLC": "Europe/Madrid", "SVQ": "Europe/Madrid", "LIS": "Europe/Lisbon", "OPO": "Europe/Lisbon",
    # Western / Central Europe
    "LHR": "Europe/London", "LGW": "Europe/London", "STN": "Europe/London", "LTN": "Europe/London",
    "MAN": "Europe/London", "DUB": "Europe/Dublin", "CDG": "Europe/Paris", "ORY": "Europe/Paris",
    "NCE": "Europe/Paris", "AMS": "Europe/Amsterdam", "BRU": "Europe/Brussels", "FRA": "Europe/Berlin",
    "MUC": "Europe/Berlin", "BER": "Europe/Berlin", "DUS": "Europe/Berlin", "HAM": "Europe/Berlin",
    "ZRH": "Europe/Zurich", "GVA": "Europe/Zurich", "VIE": "Europe/Vienna", "PRG": "Europe/Prague",
    "WAW": "Europe/Warsaw", "KRK": "Europe/Warsaw", "BUD": "Europe/Budapest", "MXP": "Europe/Rome",
    "FCO": "Europe/Rome", "VCE": "Europe/Rome", "NAP": "Europe/Rome", "CPH": "Europe/Copenhagen",
    "ARN": "Europe/Stockholm", "OSL": "Europe/Oslo", "HEL": "Europe/Helsinki", "RIX": "Europe/Riga",
    "VNO": "Europe/Vilnius", "TLL": "Europe/Tallinn", "ATH": "Europe/Athens", "SKG": "Europe/Athens",
    "LCA": "Asia/Nicosia", "PFO": "Asia/Nicosia", "IST": "Europe/Istanbul", "SAW": "Europe/Istanbul",
    "AYT": "Europe/Istanbul", "SOF": "Europe/Sofia", "OTP": "Europe/Bucharest", "BEG": "Europe/Belgrade",
    "ZAG": "Europe/Zagreb", "TBS": "Asia/Tbilisi", "EVN": "Asia/Yerevan", "BAK": "Asia/Baku", "GYD": "Asia/Baku",
    "KIV": "Europe/Chisinau", "ALA": "Asia/Almaty", "TAS": "Asia/Tashkent",
    # Americas
    "JFK": "America/New_York", "EWR": "America/New_York", "LGA": "America/New_York", "BOS": "America/New_York",
    "MIA": "America/New_York", "ATL": "America/New_York", "ORD": "America/Chicago", "DFW": "America/Chicago",
    "DEN": "America/Denver", "LAX": "America/Los_Angeles", "SFO": "America/Los_Angeles", "SEA": "America/Los_Angeles",
    "YYZ": "America/Toronto", "YUL": "America/Toronto", "YVR": "America/Vancouver", "MEX": "America/Mexico_City",
    "CUN": "America/Cancun", "BOG": "America/Bogota", "MDE": "America/Bogota", "CTG": "America/Bogota",
    "CLO": "America/Bogota", "LIM": "America/Lima", "UIO": "America/Guayaquil", "GRU": "America/Sao_Paulo",
    "GIG": "America/Sao_Paulo", "EZE": "America/Argentina/Buenos_Aires", "SCL": "America/Santiago",
    "PTY": "America/Panama", "SJO": "America/Costa_Rica", "HAV": "America/Havana",
    # Middle East / Asia / Africa
    "DXB": "Asia/Dubai", "AUH": "Asia/Dubai", "DOH": "Asia/Qatar", "AMM": "Asia/Amman", "CAI": "Africa/Cairo",
    "BKK": "Asia/Bangkok", "HKT": "Asia/Bangkok", "SIN": "Asia/Singapore", "KUL": "Asia/Kuala_Lumpur",
    "DEL": "Asia/Kolkata", "BOM": "Asia/Kolkata", "GOI": "Asia/Kolkata", "CMB": "Asia/Colombo",
    "NRT": "Asia/Tokyo", "HND": "Asia/Tokyo", "ICN": "Asia/Seoul", "HKG": "Asia/Hong_Kong",
    "PVG": "Asia/Shanghai", "PEK": "Asia/Shanghai", "TPE": "Asia/Taipei", "MNL": "Asia/Manila",
    "DPS": "Asia/Makassar", "CGK": "Asia/Jakarta", "SGN": "Asia/Ho_Chi_Minh", "HAN": "Asia/Bangkok",
    "KTM": "Asia/Kathmandu", "JNB": "Africa/Johannesburg", "CPT": "Africa/Johannesburg",
    "NBO": "Africa/Nairobi", "ADD": "Africa/Addis_Ababa", "CMN": "Africa/Casablanca", "RAK": "Africa/Casablanca",
    "SYD": "Australia/Sydney", "MEL": "Australia/Melbourne", "AKL": "Pacific/Auckland",
}


def tz_for(iata: str) -> str | None:
    return AIRPORT_TZ.get((iata or "").strip().upper())
