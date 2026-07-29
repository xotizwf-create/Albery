export type CalculatorInputs = {
  revenue: number;
  spp: number;
  cost: number;
  ads: number;
};

export type CalculatorResults = {
  taxBase: number;
  commission: number;
  payout: number;
  margin: number;
};

export function calculateResults({
  revenue,
  spp,
  cost,
  ads,
}: CalculatorInputs): CalculatorResults {
  const commission = revenue * 0.46;
  return {
    taxBase: revenue * (1 - spp / 100),
    commission,
    payout: revenue - commission - ads,
    margin: revenue - commission - cost,
  };
}
