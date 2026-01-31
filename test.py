from typing import List, Dict, Tuple
from datetime import datetime, timedelta
import random
import json
import math
from flask import Flask, jsonify, request
from pgeocode import Nominatim
from utils import Orders, Coordinates
from flask_cors import CORS

import pandas as pd

# --- KONSTANTEN ---
BIG_TRUCK = 12.0
MEDIUM_TRUCK = 7.0
SMALL_TRUCK = 3.0

# Zeit-Konstanten (in Stunden)
TIME_LOADING = 0.25          # 15 Min Beladen an der Anlage
TIME_SERVICE = 0.75          # 45 Min (0.75h) Entladen pro Kunde
SPEED_KMH = 60.0             # Durchschnittsgeschwindigkeit
START_HOUR = 8.0             # 08:00 Uhr Start
AVOID_SMALL_TRUCKS = True 

# NEU: Beton Haltbarkeit
MAX_CONCRETE_LIFESPAN = 6.0  # Nach 6 Std ab Beladung ist der Beton hart

# Koordinaten des Werks
PLANT_COORDS = {'latitude': 47.624, 'longitude': 19.0655} 


# --- HILFSFUNKTIONEN ---

def create_pools(orders: List[Orders]):
    pools = {}
    for order in orders:
        key = (order.strength, order.Dmax, order.consistency)
        if key not in pools:
            pools[key] = []
        pools[key].append(order)
    return pools


def calculate_distance(coord1: dict, coord2: dict) -> float:
    """Berechnet Distanz in KM."""
    if (math.isnan(coord1.get('latitude', 0)) or math.isnan(coord1.get('longitude', 0)) or
        math.isnan(coord2.get('latitude', 0)) or math.isnan(coord2.get('longitude', 0))):
        return 50.0 

    R = 6371
    lat1, lon1 = math.radians(coord1['latitude']), math.radians(coord1['longitude'])
    lat2, lon2 = math.radians(coord2['latitude']), math.radians(coord2['longitude'])
    
    a = math.sin((lat2 - lat1) / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_travel_time(dist_km: float) -> float:
    """Berechnet Fahrzeit mit 10% Puffer."""
    if math.isnan(dist_km): return 1.0
    return (dist_km / SPEED_KMH) * 1.1

def format_time(hours_float: float) -> str:
    if math.isnan(hours_float): return "ERROR"
    hours = int(hours_float)
    minutes = int((hours_float - hours) * 60)
    day_offset = ""
    if hours >= 24:
        days = hours // 24
        hours = hours % 24
        day_offset = f" (+{days}d)"
    return f"{hours:02d}:{minutes:02d}{day_offset}"



# --- NEUE LOGIK: PRIORISIERUNG ---

def prioritize_pool_orders(pool: List[Orders]) -> List[Orders]:
    """
    45% Chance: Bevorzugt Kunden mit großem Volumen (mehrere Trucks).
    55% Chance: Zufällige Verteilung (Random).
    """
    # Schwellenwert für "Großkunden" (mindestens ein voller mittlerer Truck)
    large_orders = [o for o in pool if o.order_volume >= MEDIUM_TRUCK]
    small_orders = [o for o in pool if o.order_volume < MEDIUM_TRUCK]

    # Entscheidungswürfel
    is_priority_mode = random.random() < 0.45

    if is_priority_mode:
        # Sortiere Großkunden nach Volumen absteigend (die Größten zuerst)
        large_orders.sort(key=lambda x: x.order_volume, reverse=True)
        # Hänge kleine Orders hinten an
        return large_orders + small_orders
    else:
        # Komplett zufälliger Mix
        combined = large_orders + small_orders
        random.shuffle(combined)
        return combined


# --- ROUTING & TRIPS ---

def optimize_stop_sequence(stop_a: dict, stop_b: dict) -> List[dict]:
    """Entscheidet: Werk->A->B oder Werk->B->A basierend auf kürzester Distanz."""
    dist_plant_a = calculate_distance(PLANT_COORDS, stop_a['coordinates'])
    dist_plant_b = calculate_distance(PLANT_COORDS, stop_b['coordinates'])
    dist_a_b = calculate_distance(stop_a['coordinates'], stop_b['coordinates'])
    
    # Check Route 1: Plant -> A -> B
    len_a_b = dist_plant_a + dist_a_b
    # Check Route 2: Plant -> B -> A
    len_b_a = dist_plant_b + dist_a_b
    
    if len_a_b <= len_b_a:
        return [stop_a, stop_b]
    else:
        return [stop_b, stop_a]

# ... deine Imports und Konstanten ...

# NEUE EINSTELLUNG:
# Setze dies auf True, um kleine LKWs zu vermeiden und stattdessen
# lieber halbleere mittlere/große LKWs zu schicken.
AVOID_SMALL_TRUCKS = True 

# --- HILFSFUNKTIONEN (Angepasst) ---

def get_best_truck(volume: float) -> Tuple[str, float]:
    """
    Entscheidet welcher Truck genommen wird.
    NEU: Wenn AVOID_SMALL_TRUCKS an ist, wird mindestens ein Medium Truck genommen.
    """
    # Fall 1: Volumen passt in einen Small Truck
    if volume <= SMALL_TRUCK:
        if AVOID_SMALL_TRUCKS:
            # Wir "upgraden" auf Medium, um Small Trucks zu sparen
            return "Medium_Truck", MEDIUM_TRUCK
        else:
            return "Small_Truck", SMALL_TRUCK
            
    # Fall 2: Passt in Medium
    elif volume <= MEDIUM_TRUCK:
        return "Medium_Truck", MEDIUM_TRUCK
        
    # Fall 3: Muss Big sein
    else:
        return "Big_Truck", BIG_TRUCK

# --- ROUTING & TRIPS (Angepasst) ---

def generate_trips_for_pool(pool: List[Orders]):
    # Schritt 0: Priorisierung (45% Regel)
    sorted_pool = prioritize_pool_orders(pool)
    
    trips = []
    leftovers = []

    # --- SCHRITT 1: Volle Ladungen (Direct Trips) ---
    for order_obj in sorted_pool:
        order = order_obj.model_dump()
        vol = order['order_volume']
        
        # Solange noch Volumen da ist...
        while vol > 0:
            current_truck_cap = 0
            current_truck_type = ""
            
            # STRATEGIE: Immer den größtmöglichen vollen Truck nehmen
            if vol >= BIG_TRUCK:
                current_truck_cap = BIG_TRUCK
                current_truck_type = "Big_Truck"
            elif vol >= MEDIUM_TRUCK:
                current_truck_cap = MEDIUM_TRUCK
                current_truck_type = "Medium_Truck"
            else:
                # Das ist ein Rest (z.B. 2.5m³ oder 5m³)
                # Wir verarbeiten ihn NICHT hier als "halbleeren" Truck, 
                # sondern schieben ihn in die Leftovers zur Optimierung (Pairing).
                leftovers.append({
                    "customer_id": order['customer_id'],
                    "volume": vol,
                    "coordinates": order['coordinates'],
                    "full_order": order
                })
                vol = 0
                continue
            
            # ... (Rest der Logik für Direct Trip wie Fahrzeit, Beton-Check bleibt gleich) ...
            dist = calculate_distance(PLANT_COORDS, order['coordinates'])
            t_travel = get_travel_time(dist)
            concrete_age = TIME_LOADING + t_travel + TIME_SERVICE
            
            if concrete_age > MAX_CONCRETE_LIFESPAN:
                print(f"WARNUNG: Kunde {order['customer_id']} zu weit weg!")
            
            duration = TIME_LOADING + t_travel + TIME_SERVICE + t_travel
            
            trips.append({
                "truck_type": current_truck_type,
                "stops": [order['customer_id']],
                "volume": current_truck_cap,
                "duration": duration,
                "concrete_age_at_finish": concrete_age,
                "route_type": "Direct"
            })
            vol -= current_truck_cap

    # --- SCHRITT 2: Reste Optimieren (Pairing) ---
    leftovers.sort(key=lambda x: x['volume'], reverse=True)
    processed_indices = set()

    for i, item_a in enumerate(leftovers):
        if i in processed_indices: continue
        
        best_match_idx = -1
        best_metric = float('inf')
        chosen_truck_type, chosen_truck_cap = "", 0
        best_stops_order = []
        best_duration = 0
        
        # Suche Partner für Pairing
        for j, item_b in enumerate(leftovers):
            if i == j or j in processed_indices: continue
            
            combined_vol = item_a['volume'] + item_b['volume']
            
            # HIER GREIFT JETZT get_best_truck MIT DER NEUEN LOGIK
            # Wenn combined_vol z.B. 4m³ ist -> get_best_truck gibt Medium (7m³) zurück.
            # Wenn combined_vol z.B. 11m³ ist -> get_best_truck gibt Big (12m³) zurück.
            
            if combined_vol > BIG_TRUCK: continue 
            
            t_type, t_cap = get_best_truck(combined_vol)
            
            stops = optimize_stop_sequence(item_a, item_b)
            
            # Zeiten berechnen
            d1 = calculate_distance(PLANT_COORDS, stops[0]['coordinates'])
            t1 = get_travel_time(d1)
            d2 = calculate_distance(stops[0]['coordinates'], stops[1]['coordinates'])
            t2 = get_travel_time(d2)
            d3 = calculate_distance(stops[1]['coordinates'], PLANT_COORDS)
            t3 = get_travel_time(d3)
            
            if (TIME_LOADING + t1 + TIME_SERVICE + t2 + TIME_SERVICE) > MAX_CONCRETE_LIFESPAN:
                continue 
            
            # Bewertung:
            waste = t_cap - combined_vol
            score = d2 + (waste * 5.0) 
            
            if score < best_metric:
                best_metric = score
                best_match_idx = j
                chosen_truck_type, chosen_truck_cap = t_type, t_cap
                best_stops_order = stops
                best_duration = TIME_LOADING + t1 + TIME_SERVICE + t2 + TIME_SERVICE + t3

        if best_match_idx != -1:
            # Shared Trip gefunden
            item_b = leftovers[best_match_idx]
            trips.append({
                "truck_type": chosen_truck_type,
                "stops": [best_stops_order[0]['customer_id'], best_stops_order[1]['customer_id']],
                "volume": item_a['volume'] + item_b['volume'],
                "duration": best_duration,
                "route_type": "Shared"
            })
            processed_indices.add(i)
            processed_indices.add(best_match_idx)
        else:
            # Kein Partner gefunden -> Muss alleine fahren
            # HIER GREIFT DIE LOGIK EBENFALLS:
            # Wenn Restvolumen = 2.0m³, gibt get_best_truck nun "Medium" (7.0) zurück statt "Small".
            
            t_type, t_cap = get_best_truck(item_a['volume'])
            
            dist = calculate_distance(PLANT_COORDS, item_a['coordinates'])
            t_travel = get_travel_time(dist)
            duration = TIME_LOADING + t_travel + TIME_SERVICE + t_travel
            
            trips.append({
                "truck_type": t_type,
                "stops": [item_a['customer_id']],
                "volume": item_a['volume'],
                "duration": duration,
                "route_type": "Leftover" # Jetzt oft mit Medium Truck für kleine Ladung
            })
            processed_indices.add(i)

    return trips

def schedule_fleet(trips: List[dict]) -> List[dict]:
    """
    Weist Trips Fahrzeugen zu. 
    Da 'trips' bereits durch die 45% Logik teilweise vorsortiert sind,
    werden wichtige Aufträge tendenziell früher in den Zeitplan geschoben.
    """
    schedule = []
    fleet = [] 
    
    plant_ready_time = START_HOUR 
    truck_id_counter = 1

    for trip in trips:
        needed_type = trip['truck_type']
        start_time = plant_ready_time
        
        # Reuse Logic
        best_truck_idx = -1
        for idx, truck in enumerate(fleet):
            if truck['type'] == needed_type and truck['available_at'] <= start_time:
                best_truck_idx = idx
                break
        
        if best_truck_idx != -1:
            assigned_truck_id = fleet[best_truck_idx]['id']
            end_time = start_time + trip['duration']
            fleet[best_truck_idx]['available_at'] = end_time
        else:
            assigned_truck_id = truck_id_counter
            truck_id_counter += 1
            end_time = start_time + trip['duration']
            fleet.append({
                'id': assigned_truck_id,
                'type': needed_type,
                'available_at': end_time
            })
            
        trip['schedule'] = {
            "truck_id": assigned_truck_id,
            "start_time": format_time(start_time),
            "end_time": format_time(end_time),
            "duration_h": round(trip['duration'], 2)
        }
        schedule.append(trip)
        plant_ready_time += TIME_LOADING

    return schedule

def create_route(orders: List[Orders]):
    pools = create_pools(orders)
    all_trips = []
    
    for pool_orders in pools.values():
        # Hier findet jetzt die 45% Priorisierung innerhalb der Pool-Generierung statt
        all_trips.extend(generate_trips_for_pool(pool_orders))
    
    # Fleet Scheduling
    scheduled_routes = schedule_fleet(all_trips)
    return scheduled_routes


orders_list = []
df = pd.read_csv('data_analysis/single_day.csv')

for i, row in df.iterrows():
    nom = Nominatim(country='HU')
    loc = nom.query_postal_code('2424')

    orders_list.append(
        Orders(
            customer_id=i + 1,
            coordinates=Coordinates(
                latitude=loc.latitude,
                longitude=loc.longitude
            ),
            order_volume=round(float(row["Volume"]), 1),
            strength=str(row["Strength"]),
            Dmax=float(row["Dmax"]),
            consistency=str(row["Consistency"]),
            exposure="medium",
            date=pd.to_datetime(row["Calendar Day"], format="%Y%m%d")
        )
    )

# ... dein bisheriger Code ...

routes = create_route(orders_list)

# --- BERECHNUNG DER STATISTIKEN ---

def calculate_statistics(generated_routes):
    total_delivered_volume = 0.0
    total_wasted_space = 0.0
    total_capacity_used = 0.0

    # Mapping der Namen zu deinen Konstanten
    truck_caps = {
        "Big_Truck": BIG_TRUCK,       # 12.0
        "Medium_Truck": MEDIUM_TRUCK, # 7.0
        "Small_Truck": SMALL_TRUCK    # 3.0
    }

    # Zähler für die Anzahl der Fahrten pro Typ
    trip_counts = {
        "Big_Truck": 0,
        "Medium_Truck": 0,
        "Small_Truck": 0
    }

    # Sets für eindeutige Truck-IDs (um die echte Flottengröße zu ermitteln)
    unique_fleet = {
        "Big_Truck": set(),
        "Medium_Truck": set(),
        "Small_Truck": set()
    }

    print("\n--- DETAILLIERTE TRIP ANALYSE ---")
    print(f"{'Truck ID':<10} {'Typ':<15} {'Ladung':<10} {'Kapazität':<10} {'Verschwendet':<10}")
    print("-" * 60)

    for trip in generated_routes:
        vol = trip['volume']
        t_type = trip['truck_type']
        t_id = trip.get('schedule', {}).get('truck_id', 'N/A')
        
        # 1. Kapazität und Waste berechnen
        cap = truck_caps.get(t_type, 0.0)
        waste = cap - vol
        if waste < 0: waste = 0.0

        total_delivered_volume += vol
        total_wasted_space += waste
        total_capacity_used += cap

        # 2. Zählen der Fahrten
        if t_type in trip_counts:
            trip_counts[t_type] += 1
        
        # 3. Speichern der eindeutigen Truck-ID für die Flottengröße
        if t_type in unique_fleet and t_id != 'N/A':
            unique_fleet[t_type].add(t_id)
        
        print(f"{t_id:<10} {t_type:<15} {vol:<10.1f} {cap:<10.1f} {waste:<10.1f}")

    # Berechnung der Auslastung in Prozent
    if total_capacity_used > 0:
        efficiency = (total_delivered_volume / total_capacity_used) * 100
    else:
        efficiency = 0.0

    print("-" * 60)
    print(f"\n--- GESAMTERGEBNIS VOLUMEN ---")
    print(f"Gesamtes geliefertes Volumen:  {total_delivered_volume:.2f} m³")
    print(f"Gesamter verschwendeter Platz: {total_wasted_space:.2f} m³")
    print(f"Flotten-Effizienz (Auslastung):{efficiency:.2f} %")

    print(f"\n--- BENÖTIGTE FLOTTE (Physische Fahrzeuge) ---")
    print(f"Hier siehst du, wie viele Fahrzeuge du tatsächlich besitzen/mieten musst:")
    print(f"{'Fahrzeug-Typ':<20} {'Anzahl':<10}")
    print("-" * 30)
    total_trucks_needed = 0
    for t_type, id_set in unique_fleet.items():
        count = len(id_set)
        total_trucks_needed += count
        print(f"{t_type:<20} {count:<10}")
    print("-" * 30)
    print(f"{'TOTAL':<20} {total_trucks_needed:<10}")

    print(f"\n--- ANZAHL DER FAHRTEN (Touren) ---")
    print(f"So oft mussten die Fahrzeuge das Werk verlassen:")
    for t_type, count in trip_counts.items():
        print(f"{t_type:<20}: {count} Fahrten")

    return total_delivered_volume, total_wasted_space

# Funktion aufrufen
vol, waste = calculate_statistics(routes)