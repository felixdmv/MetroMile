import os
import json
import math
import sys
import time
import urllib.request

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def decode_polyline(encoded, precision=6):
    factor = 10 ** precision
    index, lat, lng = 0, 0, 0
    coordinates = []
    while index < len(encoded):
        b = 0
        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coordinates.append([lat / factor, lng / factor, 800.0])
    return coordinates

snapped_cache = {}

def get_surface_coordinate(lat, lon):
    key = (lat, lon)
    if key in snapped_cache:
        return snapped_cache[key]
        
    url = f"https://router.project-osrm.org/nearest/v1/driving/{lon},{lat}?number=1"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MetroMileNearestTester/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
        waypoints = data.get('waypoints', [])
        if waypoints:
            loc = waypoints[0]['location']
            snapped = (loc[1], loc[0])
            snapped_cache[key] = snapped
            return snapped
    except Exception as e:
        print(f"    [Warning] Failed to snap {lat},{lon}: {e}")
    
    snapped_cache[key] = (lat, lon)
    return lat, lon

def query_valhalla_route(locations):
    req_json = {
        "locations": locations,
        "costing": "pedestrian",
        "directions_options": {"units": "km"}
    }
    url = "https://valhalla1.openstreetmap.de/route"
    data = json.dumps(req_json).encode('utf-8')
    req = urllib.request.Request(
        url, 
        data=data, 
        headers={
            'User-Agent': 'MetroMileRoutePrecalculator/1.0',
            'Content-Type': 'application/json'
        }
    )
    
    # Retry up to 3 times
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                raise e

def precalculate_all_routes():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, ".."))
    data_dir = os.path.join(project_root, "public", "data")
    
    cities_file = os.path.join(data_dir, "cities.json")
    if not os.path.exists(cities_file):
        print(f"No se encontró el archivo de ciudades: {cities_file}")
        return
        
    with open(cities_file, "r", encoding="utf-8") as f:
        cities = json.load(f)
        
    for city in cities:
        city_id = city["id"]
        city_dir = os.path.join(data_dir, "cities", city_id)
        metadata_file = os.path.join(city_dir, "metadata.json")
        
        if not os.path.exists(metadata_file):
            print(f"No hay metadatos para la ciudad: {city_id}")
            continue
            
        print(f"\nProcesando ciudad: {city['name']} ({city_id})...")
        
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata_list = json.load(f)
            
        updated_metadata = []
        updated_routes_full = []
        
        for route_meta in metadata_list:
            route_id = route_meta["id"]
            route_file = os.path.join(city_dir, "routes", f"{route_id}.json")
            
            if not os.path.exists(route_file):
                print(f"  [Error] No se encontró el archivo de ruta: {route_file}")
                updated_metadata.append(route_meta)
                continue
                
            with open(route_file, "r", encoding="utf-8") as f:
                route = json.load(f)
                
            stops = route.get("stops", [])
            if len(stops) < 2:
                print(f"  Ruta {route['ref']} - {route['name']}: Insuficientes paradas ({len(stops)}). Se mantiene original.")
                updated_metadata.append(route_meta)
                updated_routes_full.append(route)
                continue
            
            # Check circularity
            name_lower = route["name"].lower()
            ref_upper = route["ref"].upper()
            desc_lower = route.get("description", "").lower()
            
            is_circular = (
                "circular" in name_lower or 
                "circular" in desc_lower or 
                "anden" in name_lower or
                "andén" in name_lower or
                ref_upper in ["L6", "L12", "L12A", "L12B"]
            )
            
            routing_stops = list(stops)
            start_stop = stops[0]
            end_stop = stops[-1]
            dist_ends = haversine(start_stop["lat"], start_stop["lon"], end_stop["lat"], end_stop["lon"])
            
            if is_circular and dist_ends > 0.05:
                routing_stops.append(start_stop)
                
            # Snap all stops to surface centerlines
            snapped_locations = []
            for s in routing_stops:
                s_lat, s_lon = get_surface_coordinate(s['lat'], s['lon'])
                snapped_locations.append({"lat": s_lat, "lon": s_lon})
                time.sleep(0.01) # rate limiting polite delay
                
            success = False
            new_coords = []
            total_dist_meters = 0.0
            
            # 1. Try requesting the whole route in a single call (fast & atomic)
            try:
                res = query_valhalla_route(snapped_locations)
                trip = res.get('trip', {})
                legs = trip.get('legs', [])
                if legs:
                    for idx, leg in enumerate(legs):
                        shape = leg.get('shape', '')
                        leg_coords = decode_polyline(shape, 6)
                        if idx == 0:
                            new_coords.extend(leg_coords)
                        else:
                            new_coords.extend(leg_coords[1:])
                    total_dist_meters = trip.get('summary', {}).get('length', 0.0) * 1000.0
                    success = True
            except Exception as e:
                print(f"    [Warning] Full query failed for {route['ref']}: {e}. Trying leg-by-leg...")
                
            # 2. Leg-by-leg fallback if whole-route query fails
            if not success:
                new_coords = []
                total_dist_meters = 0.0
                for i in range(len(snapped_locations) - 1):
                    p1 = snapped_locations[i]
                    p2 = snapped_locations[i+1]
                    leg_success = False
                    try:
                        time.sleep(0.05)
                        res = query_valhalla_route([p1, p2])
                        trip = res.get('trip', {})
                        legs = trip.get('legs', [])
                        if legs:
                            shape = legs[0].get('shape', '')
                            leg_coords = decode_polyline(shape, 6)
                            if i == 0:
                                new_coords.extend(leg_coords)
                            else:
                                new_coords.extend(leg_coords[1:])
                            total_dist_meters += trip.get('summary', {}).get('length', 0.0) * 1000.0
                            leg_success = True
                    except Exception as leg_e:
                        print(f"      [Warning] Leg {i} ({routing_stops[i]['name']} -> {routing_stops[i+1]['name']}) failed: {leg_e}. Using straight-line fallback.")
                        
                    if not leg_success:
                        # Direct straight line fallback
                        lat1, lon1 = p1['lat'], p1['lon']
                        lat2, lon2 = p2['lat'], p2['lon']
                        leg_coords = [
                            [lat1, lon1, 800.0],
                            [lat2, lon2, 800.0]
                        ]
                        if i == 0:
                            new_coords.extend(leg_coords)
                        else:
                            new_coords.extend(leg_coords[1:])
                        total_dist_meters += haversine(lat1, lon1, lat2, lon2) * 1000.0
            
            total_dist_km = total_dist_meters / 1000.0
            if total_dist_km == 0.0:
                total_dist_km = route.get("distanceKm", 1.0)
                
            # Recalculate walking and running durations
            walk_time = int((total_dist_km / 4.5) * 3600)
            run_time = int((total_dist_km / 11.0) * 3600)
            
            # Update route object
            route["coords"] = new_coords
            route["distanceKm"] = round(total_dist_km, 2)
            route["estWalkingSeconds"] = walk_time
            route["estRunningSeconds"] = run_time
            
            # Save individual route JSON
            with open(route_file, "w", encoding="utf-8") as f:
                json.dump(route, f, ensure_ascii=False, indent=2)
                
            # Update metadata
            route_meta["distanceKm"] = route["distanceKm"]
            route_meta["estWalkingSeconds"] = walk_time
            route_meta["estRunningSeconds"] = run_time
            
            updated_metadata.append(route_meta)
            updated_routes_full.append(route)
            
            print(f"  [OK] {route['ref']} - {route['name']}: {len(stops)} paradas -> Distancia final: {route['distanceKm']} km")
            
        # Save updated metadata.json
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(updated_metadata, f, ensure_ascii=False, indent=2)
            
        # Save legacy single-file city JSON
        compat_file = os.path.join(data_dir, f"{city_id}.json")
        with open(compat_file, "w", encoding="utf-8") as f:
            json.dump(updated_routes_full, f, ensure_ascii=False, indent=2)
            
    print("\n¡Precalculo de rutas a pie (Valhalla) completado con éxito!")

if __name__ == "__main__":
    precalculate_all_routes()
