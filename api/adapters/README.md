# Adapter Layer (Strangler Pattern)

This package holds backward-compatibility shims while god-object modules are split into focused services.

## Rules
- New call sites should import extracted modules directly.
- Legacy entry points can call extracted modules through adapter wrappers.
- Use `deprecated_in_favor_of(...)` to log old API usage during migration.
- Remove adapter modules only after all call sites are migrated and validated.

## Typical Migration Flow
1. Extract new module and add unit tests.
2. Update legacy module to delegate through adapter.
3. Migrate callers incrementally.
4. Remove adapter when usage drops to zero.
