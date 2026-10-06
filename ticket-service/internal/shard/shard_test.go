package shard

import (
	"testing"

	"github.com/google/uuid"
)

func TestIDStable(t *testing.T) {
	id := uuid.MustParse("6f1b1c3e-8a0d-4f2e-9c11-2b7e5a4d6c80")
	if ID(id, 4) != ID(id, 4) {
		t.Fatal("shard id changed between calls")
	}
	if got := ID(id, 4); got < 0 || got > 3 {
		t.Fatalf("shard id %d out of range", got)
	}
}
