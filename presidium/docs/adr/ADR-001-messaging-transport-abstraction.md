# ADR-001: Messaging transport abstraction

## Status

Accepted for implementation.

## Context

The repository historically mixed transport logic, network logic, and UI concerns. That creates security, maintainability, and auditability issues. The product requires a clean abstraction boundary between:

- product UI,
- Presidium domain logic,
- transport implementations,
- cryptographic stack,
- and experimental decentralized layers.

## Decision

Introduce a single abstract transport interface named `MessagingTransport` and implement a first concrete `MatrixTransport`.

The transport layer owns:

- conversation creation,
- direct messaging,
- edits and deletions,
- reactions,
- typing and read receipts,
- sync and history retrieval.

The UI must call `MessagingService` or the API layer, not raw Matrix endpoints or transport internals.

## Consequences

### Positive

- UI stays product-facing and transport-agnostic.
- Matrix can become the first production transport without polluting UI code.
- Wesh can be added later as an alternative transport.
- Security review becomes easier because the critical boundary is explicit.

### Negative

- This requires a small refactor from direct protocol-specific access to the messaging abstraction.
- The first implementation is intentionally narrow and does not yet cover all premium capabilities.

## Follow-up

The next step is a real transport implementation that hooks the Matrix homeserver and validates E2EE direct messaging in test environments.
