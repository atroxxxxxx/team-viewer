package auth

import "errors"

var errMissing = errors.New("пустой логин или пароль")
var errSameUser = errors.New("логин клиента и оператора совпадают")
