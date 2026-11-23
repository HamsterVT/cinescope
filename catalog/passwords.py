"""One password policy for registration, recovery and profile settings."""
from django.core.exceptions import ValidationError

HELP = 'От 12 до 128 символов: латинские буквы a–z и A–Z, цифра 0–9 и спецсимвол, например ! или #.'
SYMBOLS = set("!@#$%^&*()_+-=[]{};'\\:\"|<>?,./`~")

def validate_password(password, confirmation=None):
    if not 12 <= len(password) <= 128:
        raise ValidationError('Пароль должен содержать от 12 до 128 символов.')
    # Supabase's configured character groups are ASCII; other letters may also
    # appear, but cannot substitute for the four required groups.
    if not (any('a' <= c <= 'z' for c in password) and any('A' <= c <= 'Z' for c in password)
            and any('0' <= c <= '9' for c in password)
            and any(c in SYMBOLS for c in password)):
        raise ValidationError(HELP)
    if confirmation is not None and password != confirmation:
        raise ValidationError('Пароли не совпадают.')
