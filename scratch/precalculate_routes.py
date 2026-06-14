import os
import json
import math
import sys
import time
import urllib.request
import urllib.parse

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

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
            
            # Prepare stops list, handle circular closure if needed
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
            
            # Check if start and end are already the same station
            start_stop = stops[0]
            end_stop = stops[-1]
            dist_ends = haversine(start_stop["lat"], start_stop["lon"], end_stop["lat"], end_stop["lon"])
            
            routing_stops = list(stops)
            if is_circular and dist_ends > 0.05:
                # Add the first stop at the end to make it a closed loop for routing
                routing_stops.append(start_stop)
                
            # Query OSRM for the entire list of stops
            coords_str = ";".join(f"{s['lon']},{s['lat']}" for s in routing_stops)
            url = f"https://router.project-osrm.org/route/v1/foot/{coords_str}?overview=full&geometries=geojson&steps=true"
            
            new_coords = []
            osrm_success = False
            total_dist_meters = 0.0
            
            try:
                # Add User-Agent to avoid blocking
                req = urllib.request.Request(url, headers={'User-Agent': 'MetroMileRouteBuilder/1.0'})
                with urllib.request.urlopen(req, timeout=15) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    
                if 'routes' in data and len(data['routes']) > 0:
                    osrm_route = data['routes'][0]
                    legs = osrm_route.get('legs', [])
                    
                    if len(legs) == len(routing_stops) - 1:
                        osrm_success = True
                        for i in range(len(legs)):
                            s1 = routing_stops[i]
                            s2 = routing_stops[i+1]
                            
                            d_straight = haversine(s1['lat'], s1['lon'], s2['lat'], s2['lon']) * 1000.0 # meters
                            leg_distance = legs[i].get('distance', d_straight)
                            
                            # Detour detection threshold
                            # If OSRM distance is 1.8x straight distance + 400m OR 2.5x straight distance, trigger fallback
                            is_detour = (leg_distance > 1.8 * d_straight + 400.0) or (leg_distance > 2.5 * d_straight)
                            
                            leg_points = []
                            if is_detour:
                                print(f"    [Detour Warning] {route['ref']} leg {s1['name']} -> {s2['name']}: OSRM={leg_distance:.1f}m vs Straight={d_straight:.1f}m. Using straight line.")
                                # Fallback to straight line
                                leg_points = [
                                    [s1['lat'], s1['lon'], 800.0],
                                    [s2['lat'], s2['lon'], 800.0]
                                ]
                                total_dist_meters += d_straight
                            else:
                                # Extract points from steps
                                steps = legs[i].get('steps', [])
                                for step in steps:
                                    step_coords = step.get('geometry', {}).get('coordinates', [])
                                    for pt in step_coords:
                                        coord = [pt[1], pt[0], 800.0] # [lat, lon, elevation]
                                        if not leg_points or leg_points[-1][:2] != coord[:2]:
                                            leg_points.append(coord)
                                            
                                # If OSRM returned empty or weird points, fallback
                                if len(leg_points) < 2:
                                    leg_points = [
                                        [s1['lat'], s1['lon'], 800.0],
                                        [s2['lat'], s2['lon'], 800.0]
                                    ]
                                    total_dist_meters += d_straight
                                else:
                                    total_dist_meters += leg_distance
                            
                            # Append to main list, avoiding duplicate boundary points
                            for pt in leg_points:
                                if not new_coords or new_coords[-1][:2] != pt[:2]:
                                    new_coords.append(pt)
                    else:
                        print(f"    [Warning] Legs count {len(legs)} doesn't match expected {len(routing_stops)-1}.")
                else:
                    print(f"    [Warning] OSRM returned no routes for {route['ref']}.")
            except Exception as e:
                print(f"    [Error] OSRM request failed for {route['ref']}: {e}")
                
            # Fallback if OSRM failed completely
            if not osrm_success or not new_coords:
                print(f"    [Fallback] Usando línea recta entre todas las estaciones para {route['ref']}.")
                new_coords = [[s["lat"], s["lon"], 800.0] for s in routing_stops]
                total_dist = 0.0
                for i in range(len(new_coords) - 1):
                    total_dist += haversine(new_coords[i][0], new_coords[i][1], new_coords[i+1][0], new_coords[i+1][1])
                total_dist_km = total_dist
            else:
                total_dist_km = total_dist_meters / 1000.0
                
            # Ensure distance is not zero
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
            
            print(f"  [OK] {route['ref']} - {route['name']}: {len(stops)} paradas -> Distancia calculada: {route['distanceKm']} km")
            time.sleep(0.1) # Friendly delay to avoid rate limit
            
        # Save updated metadata.json
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(updated_metadata, f, ensure_ascii=False, indent=2)
            
        # Save compatibility legacy single-city file (e.g. public/data/madrid.json)
        compat_file = os.path.join(data_dir, f"{city_id}.json")
        with open(compat_file, "w", encoding="utf-8") as f:
            json.dump(updated_routes_full, f, ensure_ascii=False, indent=2)
            
    print("\n¡Precalculo completado con éxito!")

if __name__ == "__main__":
    precalculate_all_routes()
