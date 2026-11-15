package session

import (
	"net"
	"testing"
)

func TestBoundPeerPermissions(t *testing.T) {
	var peers mediaPeers
	addr := &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 10123}
	tunnel := Tunnel{TunnelID: "session", Camera: true}
	unbind := peers.bind(addr, tunnel, "CLIENT")

	for _, test := range []struct {
		path    string
		publish bool
		allow   bool
	}{
		{"/tunnels/session/screen", true, true},
		{"tunnels/session/camera", true, true},
		{"tunnels/session/screen", false, false},
		{"tunnels/other/screen", true, false},
	} {
		if _, err := peers.authorize(addr, test.path, test.publish); (err == nil) != test.allow {
			t.Errorf("%v got %v", test, err)
		}
	}
	unbind()
	if _, err := peers.authorize(addr, "tunnels/session/screen", true); err == nil {
		t.Fatal("disconnected socket remains authorized")
	}

	tunnel.Camera = false
	defer peers.bind(addr, tunnel, "OPERATOR")()
	if _, err := peers.authorize(addr, "tunnels/session/screen", false); err != nil {
		t.Fatal(err)
	}
	if _, err := peers.authorize(addr, "tunnels/session/camera", false); err == nil {
		t.Fatal("camera was not enabled")
	}
	if _, err := peers.authorize(addr, "tunnels/session/screen", true); err == nil {
		t.Fatal("operator must not publish")
	}
}
