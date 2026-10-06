FROM golang:1.25-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod tidy
COPY . .
RUN CGO_ENABLED=0 go build -o /out/relay ./cmd/relay

FROM debian:bookworm-slim
COPY --from=build /out/relay /usr/local/bin/relay
USER nobody
ENTRYPOINT ["relay"]
