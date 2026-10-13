package session

import (
	"context"
	"testing"
)

func TestAllowPathShape(t *testing.T) {
	s := &Store{}
	if err := s.AllowPath(context.Background(), "/sessions/old/screen"); err != ErrDenied {
		t.Fatal("old path must be denied before redis")
	}
	if err := s.AllowPath(context.Background(), "/tunnels/not-a-uuid/screen"); err != ErrDenied {
		t.Fatal("bad uuid must be denied")
	}
}
