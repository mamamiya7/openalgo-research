export function portfolioEngineName(engine?: string): string {
  if (engine === 'openalgo') return 'OpenAlgo Rust (preview)'
  if (engine === 'nautilus') return 'NautilusTrader'
  return engine && engine !== 'vectorbt' ? engine : 'VectorBT'
}
