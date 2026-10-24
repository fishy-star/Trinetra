from app.extract.fields import extract
from app.registry import is_official_domain


def test_warning_is_not_a_demand():
    assert not extract("Police notice: do not transfer money to anyone who calls you. Rs 50,000 was lost this way.").demands_payment
    assert not extract("यदि कोई आपसे पैसे ट्रांसफ़र करने को कहे, तो कोई भुगतान न करें। ₹10,000 की ठगी हुई।").demands_payment


def test_demand_needs_payee_or_amount():
    assert not extract("Please pay at the counter.").demands_payment
    assert extract("Please pay Rs 500 at the counter.").demands_payment
    assert extract("2 घंटे में ₹1,50,000 इस खाते में भेजें।").demands_payment


def test_upi_vs_email_vs_url():
    f = extract("Pay rbi.penalty.desk@paytm. Write to registrar@gmail.com or see rbi-kyc-update.example/pay.")
    assert f.upi_ids == ["rbi.penalty.desk@paytm"]
    assert f.emails == ["registrar@gmail.com"]
    assert f.urls == ["rbi-kyc-update.example/pay"]


def test_account_and_ifsc():
    f = extract("A/c No. 50200077118843 · IFSC XYZB0004521")
    assert [(a.number, a.ifsc) for a in f.accounts] == [("50200077118843", "XYZB0004521")]


def test_official_domain_matches_registered_domain_not_substring():
    assert is_official_domain("incometax.gov.in")
    assert is_official_domain("www.rbi.org.in")
    assert not is_official_domain("rbi-kyc-update.example")
    assert not is_official_domain("gov.in.example.com")
    assert not is_official_domain("fakegov.in")


def test_otp_warning_is_not_secrecy():
    assert not extract("Never share your OTP with anyone, including the bank.").asks_secrecy
    assert extract("Do not tell anyone at home.").asks_secrecy


def test_arrest_warning_is_not_a_threat():
    assert not extract("'डिजिटल अरेस्ट' से सावधान").threatens_user
    assert not extract("No police officer arrests anyone on a video call.").threatens_user
    assert extract("A warrant for your arrest has been issued.").threatens_user


def test_deadlines():
    assert extract("Pay within 2 hours.").deadline_hours == 2
    assert extract("आज शाम 6 बजे तक जमा करें").deadline_hours == 24
    assert extract("Respond within 30 days.").deadline_hours is None
