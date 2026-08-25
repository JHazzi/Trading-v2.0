# Market Baseline V001

Primera línea predictiva de Quant Market AI:

`price_bars -> target -> market_state -> supervised_dataset -> baseline -> walk_forward`

## Filosofía

Este modelo no es el cerebro final. Es un benchmark para medir cuánto poder predictivo contiene el estado histórico del mercado sin noticias, macro, eventos ni grafos.

La salida usa cuantiles empíricos de un ensemble de Random Forest:

- Q05
- Q25
- Q50
- Q75
- Q95
- probabilidad positiva bruta

La probabilidad es **no calibrada**. La dispersión de árboles no se debe interpretar como una distribución verdadera del mercado.

## Flujo

1. Aplicar `003_target_quality.sql`.
2. Regenerar una muestra de outcomes.
3. Generar market state.
4. Entrenar un horizonte con:

```bash
python models/market/train.py --horizon 300
```

5. Ejecutar evaluación walk-forward:

```bash
python evaluation/backtest/run_walk_forward.py --horizon 300
```

## Regla metodológica

Nunca usar un split aleatorio como evaluación principal. La evaluación debe respetar el tiempo.
