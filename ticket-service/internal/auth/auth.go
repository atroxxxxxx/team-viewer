package auth

import (
	"crypto/sha256"
	"crypto/subtle"

	"github.com/google/uuid"
)

const (
	RoleClient   = "client"
	RoleOperator = "operator"
)

type Principal struct {
	Role string
	Name string
	ID   uuid.UUID
}

type Checker struct {
	clientUser   string
	clientPass   string
	operatorUser string
	operatorPass string
	operatorID   uuid.UUID
}

func New(clientUser, clientPass, operatorUser, operatorPass string) (Checker, error) {
	if clientUser == "" || clientPass == "" || operatorUser == "" || operatorPass == "" {
		return Checker{}, errMissing
	}
	if clientUser == operatorUser {
		return Checker{}, errSameUser
	}
	return Checker{
		clientUser:   clientUser,
		clientPass:   clientPass,
		operatorUser: operatorUser,
		operatorPass: operatorPass,
		operatorID:   uuid.NewSHA1(uuid.NameSpaceOID, []byte("support-operator:"+operatorUser)),
	}, nil
}

func (c Checker) Authenticate(user, pass string, ok bool) (Principal, bool) {
	if !ok {
		user, pass = "", ""
	}
	client := match(user, pass, c.clientUser, c.clientPass)
	operator := match(user, pass, c.operatorUser, c.operatorPass)
	if client {
		return Principal{Role: RoleClient, Name: c.clientUser}, true
	}
	if operator {
		return Principal{Role: RoleOperator, Name: c.operatorUser, ID: c.operatorID}, true
	}
	return Principal{}, false
}

func (c Checker) OperatorID() uuid.UUID {
	return c.operatorID
}

func match(user, pass, expectUser, expectPass string) bool {
	userSum := sha256.Sum256([]byte(user))
	expectUserSum := sha256.Sum256([]byte(expectUser))
	passSum := sha256.Sum256([]byte(pass))
	expectPassSum := sha256.Sum256([]byte(expectPass))
	userOK := subtle.ConstantTimeCompare(userSum[:], expectUserSum[:]) == 1
	passOK := subtle.ConstantTimeCompare(passSum[:], expectPassSum[:]) == 1
	return userOK && passOK
}
