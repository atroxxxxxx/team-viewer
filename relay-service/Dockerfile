FROM golang:1.25-bookworm AS build
WORKDIR /src
COPY . .
ENV GOPROXY=off
RUN CGO_ENABLED=0 go build -mod=vendor -o /out/relay ./cmd/relay

FROM debian:bookworm-slim
COPY --from=build /out/relay /usr/local/bin/relay
USER nobody
ENTRYPOINT ["relay"]
