FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# The base image ships a Yarn apt repository whose signing key has expired, which makes
# every `apt-get update` (including the docker-in-docker feature install) fail. We do not
# need Node/Yarn here, so drop the repository.
RUN rm -f /etc/apt/sources.list.d/yarn*
