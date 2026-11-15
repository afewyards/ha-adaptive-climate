# Multi-Zone

- **Mode sync:** HEAT/COOL propagates; OFF independent
- **Central controller:** Aggregates demand, 30s startup delay
- **Thermal groups:** Leader zones + feedforward for heat transfer
- **Manifolds:** Zones listed as `{entity, flow}` (L/min). Transport delay = `pipe_volume / Σ flow of heat-demand zones on the manifold`; preheat worst case = `pipe_volume / zone flow`. Feeds [pump flow control](pump-flow-control.md).
- **Migration:** entity `loops`, manifold `flow_per_loop` and plain-string zones were removed and fail validation — use `zones: [{entity: climate.x, flow: <L/min>}]`.
- **Shared outdoor temp:** Coordinator owns single EMA-filtered `outdoor_temp_lagged` (tau from `house_energy_rating`). All zones using shared weather entity read from coordinator. Zones with entity-level `outdoor_sensor` use their own raw value.
