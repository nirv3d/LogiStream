# LogiStream gold layer: metric definitions

These definitions explain how every number in the gold layer is calculated. Use them to interpret results; the numbers themselves always come from the gold tables.

## General rules

- **Source of truth for "now".** "Now" means the `as_of` time, which is the latest event time in the data lake, not the wall clock. Gold answers do not change depending on when the job is run.
- **Business day.** A day means a calendar day in Indian time (Asia/Kolkata). "Today" is the day of `as_of`, and "yesterday" is the day before it.
- **Current state.** Gold is built from the silver current-state tables. Rows with `is_deleted = true` are excluded.
- **Time window.** The data only covers the period the simulator has been running. A question about "the last 30 days" is answered over whatever days exist in the data.

## Shipments (`fact_shipments`, one row per shipment)

- **Delivered:** the shipment has an actual arrival time. A shipment without one is not delivered.
- **Late shipment:** delivered and the actual arrival is after the planned arrival.
- **On-time shipment:** delivered and the actual arrival is at or before the planned arrival. A shipment that has not arrived yet is never counted as on time.
- **On-time rate:** on-time deliveries divided by delivered shipments, times 100. Undelivered shipments are not in the denominator.
- **Delay minutes:** for a late shipment, minutes between planned and actual arrival. For an on-time shipment it is 0. For an undelivered shipment it is empty.
- **Delivery time (end-to-end):** minutes from the order's creation to the actual arrival. This is the default meaning of "average delivery time".
- **Transit time:** minutes from the shipment's `departed` event to its actual arrival.
- **Order-to-dispatch time:** minutes from order creation to the `departed` event.
- **Currently delayed:** not delivered, and the planned arrival is earlier than `as_of`. Current delay minutes is the time between the planned arrival and `as_of`.
- **Delayed event vs late shipment:** a `delayed` event in `shipment_events` is a signal recorded while the shipment is moving. It does not decide whether a shipment is late. Lateness is decided only by the arrival timestamps. `delayed_event_count` is kept as extra information.

## Orders (`fact_orders`, one row per order)

- **Order statuses:** created, packed, in_transit, delivered, cancelled.
- **Currently in transit:** orders whose status is `in_transit` right now.
- **Cancelled at:** for a cancelled order, its last update time. Cancelled orders have no shipment because they are cancelled before shipping.
- **Cancellation rate by city:** cancelled orders divided by all orders, grouped by the customer's city (`customer_city`). Delivery metrics use the destination hub's city (`destination_city`) instead.
- **Order vs shipment:** an order is the customer's request. A shipment is the physical movement. Not every order has a shipment.

## Hubs (`agg_hub_daily`, one row per hub per day)

A hub can play three roles, and each role has its own columns:

- **Destination hub:** `deliveries`, `on_time_deliveries`, `late_deliveries`, `on_time_rate_pct`, `avg_delay_minutes_late`, `avg_delivery_minutes`. These are counted on the arrival day, for shipments whose order is going to this hub.
- **Origin hub:** `shipments_dispatched`, `avg_order_to_dispatch_minutes` (also available as `avg_hub_processing_minutes`, the name to use for "hub processing time"), `orders_created`, `orders_cancelled`. Dispatches are counted on the departure day.
- **Any event hub:** `shipments_touched` is the number of distinct shipments that had a `departed`, `delivered` or `delayed` event at this hub on that day. This is the meaning of "shipments that passed through a hub". `scanned` events are ignored because they are not reliable (see below).
- **Incidents:** `incident_count` counts incident reports filed at the hub, on the day they were reported.

## Network overview (`agg_daily_overview`, one row per day)

- `orders_created`: orders created that day. `cancellation_rate_pct` is the share of those orders that ended up cancelled.
- `orders_cancelled_on_day`: orders that were cancelled on that day, whatever day they were created.
- `deliveries`, `late_deliveries`, `on_time_deliveries`, `on_time_rate_pct`: counted on the arrival day.
- `incidents`: incident reports on the day they were reported.

## Incidents (`fact_incidents`)

- One row per incident report, with the vehicle, the hub and the free-text description. Descriptions exist only in this table, so questions about what incidents say need the text, not just the counts.

## Technical terms

- **is_deleted:** a silver flag set when the source row was deleted in PostgreSQL. Deleted rows are kept in silver for history and left out of gold.
- **Silver vs gold:** silver holds clean current-state copies of the source tables. Gold holds business-ready facts and daily summaries built from silver.

## Shipment events: what each type really means

- `departed`: written once per shipment, at the origin hub, when the truck leaves.
- `delivered`: written once per shipment, at the destination hub.
- `delayed`: written when an incident happens. Its hub is the origin or the destination hub, not a live truck position.
- `scanned`: a deliberately late-stamped event at the origin hub, injected at random with a note "late scan". Its time can be hours before or after departure. It does not mark arrival at a hub and is not used in any gold metric.

## Hub processing time

- **Origin hub processing time** is `avg_order_to_dispatch_minutes`: minutes from order creation to the `departed` event. It includes packing and waiting for a free vehicle, so it is the real time the origin hub spends on an order.

## Not supported in this version

- **Processing time at a hub the shipment only passes through** (arrival to departure): shipments go directly from origin to destination, and the data has no arrival event, so this cannot be measured.
