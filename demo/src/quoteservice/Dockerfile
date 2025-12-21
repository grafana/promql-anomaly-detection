# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM php:8.3-cli@sha256:bd268e0e68ce3fadcc0cb5dbbe5363f3d5315a88049d86e676d939482add17a9 as base

ADD https://github.com/mlocati/docker-php-extension-installer/releases/latest/download/install-php-extensions /usr/local/bin/
RUN chmod +x /usr/local/bin/install-php-extensions \
  && install-php-extensions \
    opcache \
    pcntl \
    protobuf \
    opentelemetry

WORKDIR /var/www
CMD php public/index.php
USER www-data
EXPOSE ${QUOTE_SERVICE_PORT}

FROM composer:2.7@sha256:cb3483dc851665462a66c59982577dfbbde0ae2059e8b5550c2f49f44b8c333e AS vendor

WORKDIR /tmp/
COPY ./src/quoteservice/composer.json .

RUN composer install \
    --ignore-platform-reqs \
    --no-interaction \
    --no-plugins \
    --no-scripts \
    --no-dev \
    --prefer-dist

FROM base as final
COPY --from=vendor /tmp/vendor/ ./vendor/
COPY ./src/quoteservice/ /var/www

