from app.extract.mask import mask_fields, mask_text
from app.models import Account, Fields


def test_aadhaar_spaced_and_unspaced():
    assert mask_text("Aadhaar 2345 6789 1234 linked") == "Aadhaar XXXX XXXX 1234 linked"
    assert mask_text("Aadhaar 234567891234 linked") == "Aadhaar XXXX XXXX 1234 linked"


def test_pan():
    assert mask_text("PAN ABCDE1234F on file") == "PAN XXXXX1234X on file"


def test_account_number_keeps_last_four():
    assert mask_text("A/c No. 50200077118843 IFSC XYZB0004521") == "A/c No. XXXXXXXXXX8843 IFSC XYZB0004521"
    assert mask_text("transfer to 123456789 now") == "transfer to XXXXX6789 now"


def test_twelve_digit_account_is_not_read_as_aadhaar():
    assert mask_text("A/c 000912345678") == "A/c XXXXXXXX5678"


def test_mobile_numbers_are_left_alone():
    assert mask_text("call 9876543210") == "call 9876543210"


def test_mask_fields_masks_payees():
    f = mask_fields(Fields(accounts=[Account("50200077118843", "XYZB0004521")], upi_ids=["cbi.suraksha.jama@ybl"]))
    assert f.accounts == [Account("XXXXXXXXXX8843", "XYZB0004521")]
    assert f.upi_ids == ["cb***@ybl"]
