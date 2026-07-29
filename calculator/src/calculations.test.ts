import assert from 'node:assert/strict';
import test from 'node:test';

import {calculateResults} from './calculations';

test('calculates the IU example from the supplied template', () => {
  assert.deepEqual(
    calculateResults({
      revenue: 1_000_000,
      spp: 15,
      cost: 300_000,
      ads: 50_000,
    }),
    {
      taxBase: 850_000,
      commission: 460_000,
      payout: 490_000,
      margin: 240_000,
    },
  );
});

test('zero optional costs remain valid', () => {
  assert.deepEqual(
    calculateResults({
      revenue: 100_000,
      spp: 0,
      cost: 0,
      ads: 0,
    }),
    {
      taxBase: 100_000,
      commission: 46_000,
      payout: 54_000,
      margin: 54_000,
    },
  );
});
