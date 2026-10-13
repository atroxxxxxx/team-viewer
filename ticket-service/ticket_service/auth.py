import hashlib
import hmac
import uuid

NAMESPACE_OID = uuid.UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c8")


class Principal:
    def __init__(self, role: str, name: str, operator_id: uuid.UUID | None = None):
        self.role = role
        self.name = name
        self.operator_id = operator_id


class Checker:
    def __init__(self, client_user: str, client_pass: str, operator_user: str, operator_pass: str):
        if not all((client_user, client_pass, operator_user, operator_pass)):
            raise ValueError("пустые учётные данные")
        if client_user == operator_user:
            raise ValueError("логины клиента и оператора совпадают")
        self.client_user = client_user
        self.client_pass = client_pass
        self.operator_user = operator_user
        self.operator_pass = operator_pass
        self.operator_id = uuid.uuid5(NAMESPACE_OID, "support-operator:" + operator_user)

    def authenticate(self, user: str, password: str) -> Principal | None:
        if _match(user, password, self.client_user, self.client_pass):
            return Principal("client", self.client_user)
        if _match(user, password, self.operator_user, self.operator_pass):
            return Principal("operator", self.operator_user, self.operator_id)
        return None


def _match(user: str, password: str, expect_user: str, expect_pass: str) -> bool:
    user_ok = hmac.compare_digest(_sha(user), _sha(expect_user))
    pass_ok = hmac.compare_digest(_sha(password), _sha(expect_pass))
    return user_ok and pass_ok


def _sha(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()
