package session

import "testing"

func TestParseHelloRequiresRoleSecret(t *testing.T) {
	secret := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	role, clientID, got, ok := parseHello("CLIENT 11111111-1111-1111-1111-111111111111 " + secret + "\n")
	if !ok || role != "CLIENT" || clientID != "11111111-1111-1111-1111-111111111111" || got != secret {
		t.Fatalf("hello не разобран: %v %v %v %v", role, clientID, got, ok)
	}
	if _, _, _, ok := parseHello("CLIENT 11111111-1111-1111-1111-111111111111\n"); ok {
		t.Fatal("строка без секрета не должна приниматься")
	}
	if _, _, _, ok := parseHello("CLIENT not-a-uuid " + secret + "\n"); ok {
		t.Fatal("некорректный uuid не должен приниматься")
	}
}

func TestTunnelSecretIsRoleSpecific(t *testing.T) {
	client := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	operator := "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	item := Tunnel{ClientSecret: client, OperatorSecret: operator}
	if !item.allows("CLIENT", client) || !item.allows("OPERATOR", operator) {
		t.Fatal("свой секрет роли должен проходить")
	}
	if item.allows("OPERATOR", client) || item.allows("CLIENT", operator) || item.allows("CLIENT", "") {
		t.Fatal("чужой секрет не должен открывать роль")
	}
	if (Tunnel{}).allows("CLIENT", client) {
		t.Fatal("туннель без секрета должен быть закрыт")
	}
}
