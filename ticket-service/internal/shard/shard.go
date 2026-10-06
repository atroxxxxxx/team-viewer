package shard

import (
	"hash/crc32"

	"github.com/google/uuid"
)

// ID — номер секции, который считает само приложение.
// Формула фиксирована: crc32(UUID) % n. Её же читает каталог shard_directory.
func ID(accountID uuid.UUID, n int) int {
	if n <= 0 {
		return 0
	}
	sum := crc32.ChecksumIEEE(accountID[:])
	return int(sum % uint32(n))
}
