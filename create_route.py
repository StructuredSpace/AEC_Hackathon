from typing import List, Dict, Tuple
from datetime import datetime, timedelta
import random
import json
import math
from flask import Flask, jsonify, request
from pgeocode import Nominatim
from utils import Orders, Coordinates
from flask_cors import CORS


app = Flask(__name__)
CORS(app, origins=["http://localhost:5173"])
DB_FILE = 'orders_db.json'

# --- KONSTANTEN ---
BIG_TRUCK = 12.0
MEDIUM_TRUCK = 7.0
SMALL_TRUCK = 3.0

# Zeit-Konstanten (in Stunden)
TIME_LOADING = 0.25          # 15 Min Beladen an der Anlage
TIME_SERVICE = 0.75          # 45 Min (0.75h) Entladen pro Kunde
SPEED_KMH = 60.0             # Durchschnittsgeschwindigkeit
START_HOUR = 8.0             # 08:00 Uhr Start

# NEU: Beton Haltbarkeit
MAX_CONCRETE_LIFESPAN = 6.0  # Nach 6 Std ab Beladung ist der Beton hart

# Koordinaten des Werks
PLANT_COORDS = {'latitude': 47.624, 'longitude': 19.0655} 


# --- HILFSFUNKTIONEN ---

def create_pools(orders: List[Orders]):
    pools = {}
    for order in orders:
        key = (order.strength, order.Dmax, order.consistency, order.exposure)
        if key not in pools:
            pools[key] = []
        pools[key].append(order)
    return pools

def load_orders():
    try:
        with open(DB_FILE, 'r') as f:
            data = json.load(f)
            return [Orders.model_validate(d) for d in data]
    except FileNotFoundError:
        return []

def save_orders(orders: List[Orders]):
    with open(DB_FILE, 'w') as f:
        json.dump(
            [o.model_dump(mode="json") for o in orders],
            f,
            indent=2
        )

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

def get_best_truck(volume: float) -> Tuple[str, float]:
    if volume <= SMALL_TRUCK:
        return "Small_Truck", SMALL_TRUCK
    elif volume <= MEDIUM_TRUCK:
        return "Medium_Truck", MEDIUM_TRUCK
    else:
        return "Big_Truck", BIG_TRUCK


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

def generate_trips_for_pool(pool: List[Orders]):
    # Schritt 0: Sortierung anwenden (45% Regel)
    sorted_pool = prioritize_pool_orders(pool)
    
    trips = []
    leftovers = []

    # --- SCHRITT 1: Volle Ladungen (Direct Trips) ---
    for order_obj in sorted_pool:
        order = order_obj.model_dump()
        vol = order['order_volume']
        
        # Solange wir volle Trucks füllen können
        while vol > 0:
            current_truck_cap = 0
            current_truck_type = ""
            
            # Entscheidung LKW Größe (Big First Strategy)
            if vol >= BIG_TRUCK:
                current_truck_cap = BIG_TRUCK
                current_truck_type = "Big_Truck"
            elif vol >= MEDIUM_TRUCK:
                current_truck_cap = MEDIUM_TRUCK
                current_truck_type = "Medium_Truck"
            else:
                # Rest für Optimierung aufheben (kein Split hier, um "max 2 deliveries" Regel nicht zu verletzen)
                leftovers.append({
                    "customer_id": order['customer_id'],
                    "volume": vol,
                    "coordinates": order['coordinates'],
                    "full_order": order
                })
                vol = 0
                continue
            
            # Beton Lebensdauer Check für Direct Trip
            dist = calculate_distance(PLANT_COORDS, order['coordinates'])
            t_travel = get_travel_time(dist)
            
            # Beton Alter bei Ankunft + Entladen
            concrete_age = TIME_LOADING + t_travel + TIME_SERVICE
            
            if concrete_age > MAX_CONCRETE_LIFESPAN:
                # Theoretisch unmöglich zu liefern -> Error oder Special Handling
                # Hier: Trotzdem planen aber markieren
                print(f"WARNUNG: Kunde {order['customer_id']} ist zu weit weg! Beton härtet aus.")
            
            # Gesamtdauer für LKW (inkl Rückfahrt)
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
    # Reste sortieren wir immer nach Größe, um die großen Brocken zuerst unterzubringen
    leftovers.sort(key=lambda x: x['volume'], reverse=True)
    processed_indices = set()

    for i, item_a in enumerate(leftovers):
        if i in processed_indices: continue
        
        best_match_idx = -1
        best_metric = float('inf')
        chosen_truck_type, chosen_truck_cap = "", 0
        best_stops_order = []
        best_duration = 0
        
        # Suche Partner
        for j, item_b in enumerate(leftovers):
            if i == j or j in processed_indices: continue
            
            combined_vol = item_a['volume'] + item_b['volume']
            # Maximale Ladung prüfen
            if combined_vol > BIG_TRUCK: continue 
            
            # Truck Typ bestimmen
            t_type, t_cap = get_best_truck(combined_vol)
            
            # Route berechnen (A->B oder B->A)
            stops = optimize_stop_sequence(item_a, item_b)
            
            # ZEITEN BERECHNEN (Kritisch für 6h Regel)
            # Werk -> Stop 1
            d1 = calculate_distance(PLANT_COORDS, stops[0]['coordinates'])
            t1 = get_travel_time(d1)
            # Stop 1 -> Stop 2
            d2 = calculate_distance(stops[0]['coordinates'], stops[1]['coordinates'])
            t2 = get_travel_time(d2)
            # Stop 2 -> Werk
            d3 = calculate_distance(stops[1]['coordinates'], PLANT_COORDS)
            t3 = get_travel_time(d3)
            
            # Wann ist der Beton beim 2. Kunden fertig verarbeitet?
            # Laden + Fahrt1 + Service1 + Fahrt2 + Service2
            concrete_age_at_end = TIME_LOADING + t1 + TIME_SERVICE + t2 + TIME_SERVICE
            
            if concrete_age_at_end > MAX_CONCRETE_LIFESPAN:
                continue # Diese Kombi geht nicht, Beton wird hart
            
            # Bewertung der Route (Distanz + Leerraum)
            waste = t_cap - combined_vol
            # Distanz zwischen Kunden ist relevant
            score = d2 + (waste * 5.0) 
            
            if score < best_metric:
                best_metric = score
                best_match_idx = j
                chosen_truck_type, chosen_truck_cap = t_type, t_cap
                best_stops_order = stops
                # Gesamtdauer für den Truck (inkl Rückfahrt)
                best_duration = TIME_LOADING + t1 + TIME_SERVICE + t2 + TIME_SERVICE + t3

        if best_match_idx != -1:
            # Shared Trip erstellen
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
            # Muss alleine fahren (Leftover Trip)
            t_type, t_cap = get_best_truck(item_a['volume'])
            dist = calculate_distance(PLANT_COORDS, item_a['coordinates'])
            t_travel = get_travel_time(dist)
            duration = TIME_LOADING + t_travel + TIME_SERVICE + t_travel
            
            trips.append({
                "truck_type": t_type,
                "stops": [item_a['customer_id']],
                "volume": item_a['volume'],
                "duration": duration,
                "route_type": "Leftover"
            })
            processed_indices.add(i)

    return trips


def create_route(orders: List[Orders]):
    pools = create_pools(orders)
    all_trips = []
    
    for pool_orders in pools.values():
        # Hier findet jetzt die 45% Priorisierung innerhalb der Pool-Generierung statt
        all_trips.extend(generate_trips_for_pool(pool_orders))
    
    # Fleet Scheduling
    scheduled_routes = schedule_fleet(all_trips)
    return scheduled_routes


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


# --- API & STARTUP ---

@app.route('/orders', methods=['GET'])
def get_orders():
    orders = load_orders()
    return jsonify([o.to_dict() for o in orders])

@app.route('/orders', methods=['POST'])
def add_order():
    data = request.json
    try:
        orders = load_orders()
        customer_id = max([o.customer_id for o in orders], default=0) + 1
        
        nom = Nominatim(country=data['country'])
        loc = nom.query_postal_code(data['postal_code'])
        
        if math.isnan(loc.latitude) or math.isnan(loc.longitude):
            return jsonify({"error": "Invalid postal code"}), 400

        new_order = Orders(
            coordinates=Coordinates(latitude=loc.latitude, longitude=loc.longitude),
            customer_id=customer_id,
            order_volume=data['order_volume'],
            strength=data['strength'],
            Dmax=data['Dmax'],
            consistency=data['consistency'],
            exposure=data['exposure'],
            date=datetime.fromisoformat(data['date'])
        )
        orders.append(new_order)
        save_orders(orders)
        return jsonify(new_order.model_dump()), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/schedule', methods=['PUT'])
def update_route():
    orders = load_orders()
    if not orders: return jsonify([]), 200
    routes = create_route(orders)
    return jsonify(routes), 200

def generate_initial_orders():
    orders_list = []
    start_date = datetime(2025, 11, 22)
    strength_options = ['low', 'medium', 'high']
    lat_center, lon_center = 50.0, 10.0

    for i in range(15):
        # Generiere diverse Volumen, um Logik zu testen
        # Mix aus kleinen (2), mittleren (8) und großen (20) Orders
        vol_base = [2, 4, 8, 11, 15, 24]
        vol = random.choice(vol_base) + random.random()
        
        orders_list.append(Orders(
            customer_id=i+1,
            coordinates=Coordinates(latitude=lat_center + (random.random()-0.5), longitude=lon_center + (random.random()-0.5)),
            order_volume=round(vol, 1),
            strength=random.choice(strength_options),
            Dmax=50,
            consistency='consistent',
            exposure='medium',
            date=start_date
        ))
    save_orders(orders_list)

if __name__ == '__main__':
    if not load_orders():
        generate_initial_orders()
    app.run(debug=True)