package auth

import "testing"

func TestAuthenticate(t *testing.T) {
	checker, err := New("client", "client-secret-value", "operator", "operator-secret-value")
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := checker.Authenticate("client", "wrong-password", true); ok {
		t.Fatal("чужой пароль принят")
	}
	principal, ok := checker.Authenticate("client", "client-secret-value", true)
	if !ok || principal.Role != RoleClient {
		t.Fatal("клиент не вошёл")
	}
	principal, ok = checker.Authenticate("operator", "operator-secret-value", true)
	if !ok || principal.Role != RoleOperator || principal.ID == [16]byte{} {
		t.Fatal("оператор не вошёл")
	}
	if _, ok := checker.Authenticate("client", "client-secret-value", false); ok {
		t.Fatal("пустой заголовок принят")
	}
}
