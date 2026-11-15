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
    def __init__(self, client_user: str, client_pass: str, operator_user: str, operator_pass: str,
                 operator_accounts: dict[str, str] | None = None):
        if not (client_user and client_pass):
            raise ValueError("пустые учётные данные клиента")
        self.operators = dict(operator_accounts or {})
        if operator_user and operator_pass:
            if operator_user in self.operators:
                raise ValueError("повторяющийся логин оператора")
            self.operators[operator_user] = operator_pass
        if not self.operators or any(not user or not password or ":" in user for user, password in self.operators.items()):
            raise ValueError("нужны отдельные логины и пароли операторов")
        if client_user in self.operators:
            raise ValueError("логины клиента и оператора совпадают")
        self.client_user = client_user
        self.client_pass = client_pass

    def authenticate(self, user: str, password: str) -> Principal | None:
        if _match(user, password, self.client_user, self.client_pass):
            return Principal("client", self.client_user)
        secret = self.operators.get(user)
        if secret is not None and _match(user, password, user, secret):
            operator_id = uuid.uuid5(NAMESPACE_OID, "support-operator:" + user)
            return Principal("operator", user, operator_id)
        return None


def _match(user: str, password: str, expect_user: str, expect_pass: str) -> bool:
    user_ok = hmac.compare_digest(_sha(user), _sha(expect_user))
    pass_ok = hmac.compare_digest(_sha(password), _sha(expect_pass))
    return user_ok and pass_ok


def _sha(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()
