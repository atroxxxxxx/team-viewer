package session

import (
	"context"
	"net"
	"strings"
	"sync"
)

type mediaPeer struct {
	tunnelID string
	role     string
	camera   bool
}

type mediaPeers struct{ connections sync.Map }

func (p *mediaPeers) bind(addr net.Addr, tunnel Tunnel, role string) func() {
	key := addr.String()
	p.connections.Store(key, mediaPeer{tunnelID: tunnel.TunnelID, role: role, camera: tunnel.Camera})
	return func() { p.connections.Delete(key) }
}

func (p *mediaPeers) authorize(addr net.Addr, path string, publishing bool) (mediaPeer, error) {
	value, found := p.connections.Load(addr.String())
	if !found {
		return mediaPeer{}, ErrDenied
	}
	peer := value.(mediaPeer)
	role := "OPERATOR"
	if publishing {
		role = "CLIENT"
	}
	base := "tunnels/" + peer.tunnelID + "/"
	path = strings.TrimPrefix(path, "/")
	if peer.role != role || (path != base+"screen" && !(peer.camera && path == base+"camera")) {
		return mediaPeer{}, ErrDenied
	}
	return peer, nil
}

func (s *Store) AllowPeer(ctx context.Context, addr net.Addr, path string, publishing bool) error {
	peer, err := s.peers.authorize(addr, path, publishing)
	if err != nil {
		return err
	}
	_, err = s.ByTunnel(ctx, peer.tunnelID)
	return err
}
