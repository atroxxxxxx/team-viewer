package token

import "testing"

func TestAllows(t *testing.T) {
	g := Grant{Role: "publisher", Paths: []string{"/sessions/a/screen"}}
	if !Allows(g, "publisher", "/sessions/a/screen") {
		t.Fatal("expected allow")
	}
	if Allows(g, "reader", "/sessions/a/screen") {
		t.Fatal("reader must not use a publisher token")
	}
	if Allows(g, "publisher", "/sessions/a/camera") {
		t.Fatal("camera was not granted")
	}
}
