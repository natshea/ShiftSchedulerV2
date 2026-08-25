# optimizer.py  — add this behavior inside build_and_solve_shift_model (at the end, after solve)

import pulp
import time
from utils import week_of, weekday_of

def build_and_solve_shift_model(
    W,                      # list: workers
    D,                      # list: 28 days
    T,                      # list: currently 18 time slots
    S,                      # valid (s, e) shift pairs
    MinHw, MaxHw,           # dicts: weekly minimum and maximum hours
    WeekendOnly,            # dicts: boolean for if worker is weekend only (1) or not (0)
    Demand,                 # dict: number of staff needed for each day-slot
    Rolew,                  # dict: worker-role pairs
    Locw,                   # dict: worker-location pairs
    priority_slots=None,    # priority slots (u,d,t)
    M=3,                    # penalty weight for ILM slack variable; i.e., # hours that can be without shift adult
    N=1,                    # penalty weight for CLM slack variable; i.e., # hours that can be without on-duty adult
    Max_Deviation=3,        # per location per week
    time_limit=86400,        # originally 120 # 6000 didn't work for all noxnoy without mgmt separated
    unavailability=None,    # worker-day-time slots for unavailability
    constraints_flag=None   # turn on/off certain constraints
):
    model = pulp.LpProblem("Shift_Scheduling", pulp.LpMinimize)

    # --------------------------------------------------------------------
    # Sets
    # --------------------------------------------------------------------

    shift_adult = {"Supervisor", "Shop Manager", "Team Leader", "Shift Manager"} # intra-location management constraint
    onduty_adult = {"General Manager", "Supervisor", "Shop Manager", "Team Leader"} # cross-location management


    ### U subsets
    U = list(set(Locw[w] for w in W)) # all locations (e.g., CATEDRAL, ALCAZAR, ..., PLAZA NUEVA, MANAGEMENT)
    U_store = [u for u in U if u != "MANAGEMENT"] # locations without MANAGEMENT (e.g., CATEDRAL, ALCAZAR, ..., PLAZA NUEVA)

    ### W subsets & related sets

    # if supervisor constraint is removed, remove supervisors entirely except for the Catedral only supervisor entry:
    SUP_1LOC = "Supervisor-CATEDRALonly" # Update if: supervisor name changes in staff_df file

    if constraints_flag["C12"] and len(U_store) != 1: 
        W = [w for w in W if w != SUP_1LOC]
        total_sup_hours = sum(MaxHw[w] for w in W if Rolew[w].lower() == "supervisor") # total hours across supervisors if C12 is on
    else: # if C12 off or only 1 loc (+mgmt), assign JAVI S only to Catedral for 20hrs per week
        W = [w for w in W if Rolew[w] != "Supervisor" or w == SUP_1LOC]

    # Main W_u
    W_u = {} # W_location - workers in location u
    for w in W:
        u = Locw[w]
        W_u.setdefault(u, []).append(w)

    K = list(range(1, 5))  # index for weeks 1-4

    W_SA = {u: [w for w in w_name if Rolew[w] in shift_adult] # workers with "shift adult" roles
            for u, w_name in W_u.items()}
    W_OA = {u: [w for w in w_name if Rolew[w] in onduty_adult] # workers with "on-duty adult" roles
            for u, w_name in W_u.items()}
    W_Su = {u: [w for w in w_name if Rolew[w] == "Supervisor"] # workers who are supervisors
            for u, w_name in W_u.items()}
    W_WE = {u: [w for w in w_name if WeekendOnly[w]] # workers who work only on weekends, i.e., have 15-hr contracts
            for u, w_name in W_u.items()}
    
    # D subsets
    D_k = {k: [d for d in D if week_of(d) == k] for k in K} # dictionary of lists of days in each week
    D_WE = [d for d in D if weekday_of(d) >=5] # list of weekend days (Friday/Saturday/Sunday days)

    # S subsets
    S_set = list(set(S)) # all possible time slots

    S_demand = {} # all time slots that have Demand > 0 for each location/day
    opening_slot = {} # all time slots that are closing for each store
    closing_slot = {} # all time slots that are closing for each store
    S_ud = {} # all feasible shifts (when store is open) for each location/day
    slot_cover_ud = {} # slot_cover[t] --> slot_cover_ud[(u, d, t)]
    for u in U_store:
        for d in D:
            demand_slots = [t for t in T if Demand[(u, d, t)] > 0]
            S_demand[(u, d)] = demand_slots

            # If store is closed all day
            if demand_slots:
                # Opening and closing time slots
                open_t = demand_slots[0] # min
                close_t = demand_slots[-1] # max
            else:
                open_t = None
                close_t = None

            opening_slot[(u, d)] = open_t
            closing_slot[(u, d)] = close_t

            # Feasible shifts
            if open_t is None:
                S_ud[(u, d)] = []
            else: 
                S_ud[(u, d)] = [(s, e) for (s, e) in S_set if s >= open_t and e <= close_t]

            # Feasible shifts over each slot
            for t in T:
                slot_cover_ud[(u, d, t)] = [(s, e) for (s, e) in S_ud[(u, d)] if s <= t <= e]


    # P for priority slots
    if priority_slots is None:
        priority_slots = {}

    P = {
        (u,d,t) for u, slots in priority_slots.items()
        for (d,t) in slots
        if u in U_store and d in D and t in T
    }    


    # --------------------------------------------------------------------
    # Decision variables
    # --------------------------------------------------------------------

    z, v, b, under, over, ILM = {}, {}, {}, {}, {}, {}

    for u in U_store:
        if constraints_flag["C12"] and len(U_store) != 1:
            z[u] = pulp.LpVariable.dicts(
                            f"z_{u}", 
                            ([w for w in W_u[u] if Rolew[w] != "Supervisor"], D), # if C12 on, don't include Supervisors; else keep
                            cat="Binary"
                            )
            v[u] = pulp.LpVariable.dicts(
                            f"v_{u}", 
                            ([w for w in W_u[u] if Rolew[w] != "Supervisor"], K), # if C12 on, don't include Supervisors; else keep
                            cat="Binary"
                            )
        else:
            z[u] = pulp.LpVariable.dicts(f"z_{u}", (W_u[u], D), cat="Binary")
            v[u] = pulp.LpVariable.dicts(f"v_{u}", (W_u[u], K), cat="Binary")            
        

        b[u] = {}
        for w in W_u[u]:
            b[u][w] = {}
            for d in D:
                b[u][w][d] = {
                    (s, e): pulp.LpVariable(f"b_{u}_{w}_{d}_{s}_{e}", cat="Binary")
                    for (s, e) in S_ud[(u, d)]
                }
        
        under[u] = pulp.LpVariable.dicts(f"under_{u}", (D, T), lowBound=0)
        over[u]  = pulp.LpVariable.dicts(f"over_{u}",  (D, T), lowBound=0)

        ILM[u] = {}
        for d in D:
            ILM[u][d] = {}
            for t in S_demand[(u, d)]:
                if Demand[(u, d, t)] > 0:
                    ILM[u][d][t] = pulp.LpVariable(f"ILM_SA_{u}_{d}_{t}", cat="Binary")

    CLM = pulp.LpVariable.dicts(f"CLM_OA", (D, T), cat="Binary") # soften C10 - should be 1 just for the 01:00 - 02:00 slot

    # Supervisor-specific decision variables (when C12 on)
    if constraints_flag["C12"] and len(U_store) != 1:
        z_sup = pulp.LpVariable.dicts("z_supervisor", D, cat="Binary") # For supervisor-specific C8
        v_sup = pulp.LpVariable.dicts("v_supervisor", K, cat="Binary") # For supervisor-specific C13

    # substitutes for b[u][w][d][(s, e)] for (s, e) in S_ud[(u, d)]
    worked_day = {} 
    for u in U_store:
        worked_day[u] = {}
        for w in W_u[u]:
            worked_day[u][w] = {}
            for d in D:
                worked_day[u][w][d] = pulp.lpSum(b[u][w][d][(s, e)] for (s, e) in S_ud[(u, d)])


    # --------------------------------------------------------------------
    # Objective function
    # --------------------------------------------------------------------

    model += (
        pulp.lpSum(under[u][d][t] + over[u][d][t] for u in U_store for d in D for t in T) 
        + M/10 * pulp.lpSum(var for u in U_store for d in D for var in ILM[u][d].values())
        + N/10 * pulp.lpSum(CLM[d][t] for d in D for t in T)
        )
    
    # --------------------------------------------------------------------
    # Constraints
    # --------------------------------------------------------------------

    ########## TURNING CONSTRAINTS ON/OFF ##########
    if constraints_flag is None:
        constraints_flag = {f"C{i}": True for i in range(1, len(constraints_flag) + 1)}


    ### MGMT: Pre-computing "MANAGEMENT" assignment
    if "MANAGEMENT" in U:
        mgmt_cov = {} # for C10
        for d in D:
            for t in T:
                mgmt_cov[(d, t)] = 1 if Demand[("MANAGEMENT", d, t)] > 0 else 0

        mgmt_shift = {} # for General Management shift assignment
        mgmt_worker = W_u["MANAGEMENT"][0] # General Manager
        for d in D:
            slots = [t for t in T if Demand[("MANAGEMENT", d, t)] > 0]
            if slots:
                mgmt_shift[d] = {
                    "name": mgmt_worker, 
                    "location": "MANAGEMENT", 
                    "day": d,
                    "start_slot": min(slots),
                    "end_slot": max(slots)
                }
            else:
                mgmt_shift[d] = None
    else: # have 0/None
        mgmt_cov = {} # for C10
        for d in D:
            for t in T:
                mgmt_cov[(d, t)] = 0
        mgmt_shift = {} # for General Management shift assignment
        for d in D:
            mgmt_shift[d] = None


    ### C1: weekly contract hours
    if constraints_flag["C1"]:
        for u in U_store:
            for w in W_u[u]:
                for k in K:
                    weekly_hours = pulp.lpSum(
                                        (e - s + 1) * b[u][w][d][(s, e)] 
                                        for d in D_k[k] for (s, e) in S_ud[(u, d)]
                                        )
                    model += weekly_hours >= MinHw[w]
                    model += weekly_hours <= MaxHw[w]


    ### C2: closing-shift limits
    if constraints_flag["C2"]:
        for u in U_store:
            for w in W_u[u]:
                if constraints_flag["C12"] and len(U_store) != 1 and Rolew[w] == "Supervisor": # skip weekend-only workers and Supervisors (supervisors have this in C12)
                    continue
                for k in K:
                    model += pulp.lpSum(b[u][w][d][(s, e)] 
                                        for d in D_k[k] if (u, d) in closing_slot 
                                        for (s, e) in S_ud[(u, d)] if e == closing_slot[(u, d)]
                                        ) <= 2


    ### C3: minimum staffing per slot (if demand != 0)
    if constraints_flag["C3"]:
        for u in U_store:
            for d in D:
                for t in S_demand[(u, d)]:
                    # if Demand[(u, d, t)] == 0: # skip if no staff demanded
                    #     continue
                    # if Demand[(u, d, t)] > 0:
                    #     model += pulp.lpSum(b[u][w][d][(s, e)] for w in W_u[u] for (s, e) in slot_cover_ud[(u, d, t)]) >= 1 
                    model += pulp.lpSum(b[u][w][d][(s, e)] for w in W_u[u] for (s, e) in slot_cover_ud[(u, d, t)]) >= 1 

 
    ### C4: demand coverage with deviation bounds
    if constraints_flag["C4"]:
        for u in U_store:
            for k in K:
                max_dev = Max_Deviation[u][k] if isinstance(Max_Deviation, dict) else Max_Deviation
                for d in D_k[k]:
                    for t in S_demand[(u, d)]: 
                        model += (
                            pulp.lpSum(b[u][w][d][(s, e)] for w in W_u[u] for (s, e) in slot_cover_ud[(u, d, t)]) 
                            + under[u][d][t] - over[u][d][t] == Demand[(u, d, t)]
                        )
                        model += under[u][d][t] + over[u][d][t] <= max_dev


    ### C5: one shift per worker per day
    if constraints_flag["C7"]:
        for u in U_store:
            for w in W_u[u]:
                for d in D:
                    model += pulp.lpSum(b[u][w][d][(s, e)] for (s, e) in S_ud[(u, d)]) <= 1


    ### C6: late-to-early shift avoidance
    forbidden = { # forbidden shift indexes after a slot contains the key
                15: {1,2,3},
                16: {1,2,3,4},
                17: {1,2,3,4,5},
                18: {1,2,3,4,5,6},
            }
    
    if constraints_flag["C8"]:
        for u in U_store:
            for w in W_u[u]:
                if constraints_flag["C12"] and len(U_store) != 1 and Rolew[w] == "Supervisor": # skip weekend-only workers and Supervisors (supervisors have this in C12)
                    continue
                for d in D[:-1]:
                    for e, starts in forbidden.items():
                        model += (
                            pulp.lpSum(b[u][w][d][shift] for shift in S_ud[(u,d)] if shift[1] == e)
                            + pulp.lpSum(b[u][w][d+1][shift] for shift in S_ud[(u,d+1)] if shift[0] in starts) <= 1
                        )


    ### C7: weekend-only rule for 15-hour workers
    if constraints_flag["C9"]:
        for u in U_store:
            for w in W_WE[u]:
                for d in [day for day in D if day not in D_WE]: 
                    model += pulp.lpSum(b[u][w][d][(s,e)] for (s,e) in S_ud[(u, d)]) == 0 # alt-y


    ### C8: two consecutive rest days
        # Note: no C8g (Sundays can be isolated rest days; still have ≥2 consecutive rest days in a week)
    if constraints_flag["C8"]:
        for u in U_store:
            for w in W_u[u]:
                if WeekendOnly[w] or (constraints_flag["C12"] and len(U_store) != 1 and Rolew[w] == "Supervisor"): # skip weekend-only workers and Supervisors (supervisors have this in C12)
                    continue
                for k in K:
                    d_last = D_k[k][-1] # last day of week k (Sunday)
                    
                    # C8a: at most 5 working days per week
                    model += pulp.lpSum(worked_day[u][w][d] for d in D_k[k]) <= 5
                    
                    # C8b: Fix z=0 on last day of week since a rest block cannot start here, as d+1 crosses into the next week
                    model += z[u][w][d_last] == 0

                    # C8f: at least 1 set of rest days per week
                    model += pulp.lpSum(z[u][w][d] for d in D_k[k]) >= 1

                    for d in D_k[k]:
                        if d == d_last: # can't start z on the Sunday/last day of week
                            continue
                        # C8c-e:
                        model += z[u][w][d] <= 1 - worked_day[u][w][d] # C8c - if z=1, d must be rest day
                        model += z[u][w][d] <= 1 - worked_day[u][w][d+1] # C8d - if z=1, d+1 must be rest day
                        model += z[u][w][d] >= 1 - worked_day[u][w][d] - worked_day[u][w][d+1] # C8e - if d and d+1 rest days, z must be 1

                        # C8h: z's that cover day d; sum of rest days = rest block size
                        cover_z = [] # find which z variables could cover day d
                        if d != d_last and d in D_k[k]: # if d is not Sunday and is a day in that week k
                            cover_z.append(z[u][w][d]) # rest block starts at d
                        if d - 1 in D_k[k] and d - 1 != d_last: # if d-1 is in that week and is not Sunday
                            cover_z.append(z[u][w][d - 1]) # rest block starts at d-1

                        if cover_z:
                            model += 1 - worked_day[u][w][d] <= pulp.lpSum(cover_z) # C8g: if d is rest day, at least z[d] or z[d-1] =1
                                # NOTE: this version allows Sunday to be an isolated rest day, but there's always at least another 2-day block
                                    # also not enforcing no isolated rest days for Mondays
                        else:
                            pass # z[d] and z[d-1] = 0; could be d=d_last, cover_z>0 only if Saturday is a rest day
                    

    ### C9: intra-location management
    if constraints_flag["C9"]:
        for u in U_store:
            for d in D:
                for t in S_demand[(u, d)]:
                    model += pulp.lpSum(b[u][w][d][(s, e)] for w in W_SA[u] for (s, e) in slot_cover_ud[(u, d, t)]) + ILM[u][d][t] >= 1 # C9a
                for t in T[:-(M)]:
                    C9_range = [t_ for t_ in range(t, t + M + 1) if t_ in ILM[u][d]]
                    if len(C9_range) > 1:
                        model += pulp.lpSum(ILM[u][d][t_] for t_ in C9_range) <= M # C9b


    ### C10: cross-location management
    if constraints_flag["C10"]:
        allstore_open_slot = {}
        for d in D:
            open_slots = set()
            for u in U_store:
                open_slots.update(S_demand[(u, d)])
            allstore_open_slot[d] = sorted(open_slots)

        UB12_dt = {
            (d, t): sum(1 for u in U_store for w in W_OA[u] if slot_cover_ud[(u, d, t)]) for d in D for t in T
        }
        
        for d in D:
            if len(U) == 1: # if only a solo location
                continue
            for t in allstore_open_slot[d]:
                C10_coverage = (
                    mgmt_cov[(d, t)] 
                    + pulp.lpSum(b[u][w][d][(s, e)] for u in U_store for w in W_OA[u] for (s, e) in slot_cover_ud[(u, d, t)])
                    )
                model += C10_coverage + CLM[d][t] >= 1 # if coverage=0, CLM=1
                model += C10_coverage <= UB12_dt[(d, t)] * (1 - CLM[d][t]) # if CLM=1, coverage=0
            for t in T[:-(N)]: # prevent N consecutive hours
                C10_range = [t_ for t_ in range(t, t + N + 1) if t_ in CLM[d]]
                if len(C10_range) > 1:
                    model += pulp.lpSum(CLM[d][t_] for t_ in C10_range) <= N


    ### C11: priority shift management
    if constraints_flag["C11"]:
        for (u,d,t) in P:
            model += ILM[u][d][t] == 0 # C11a: fixes the ILM slack variable to 0 for priority slots
            model += pulp.lpSum(b[u][w][d][(s, e)] for w in W_SA[u] for (s, e) in slot_cover_ud[(u, d, t)]) >= 1 # C11b


    ### C12 :supervisor shifts
    if constraints_flag["C12"] and len(U_store) != 1:
        for u in U_store: # C12a: at least 1 supervisor shift per location per week # sup_loc --> U_store?
            w = W_Su[u][0]
            for k in K:
                model += pulp.lpSum(b[u][w][d][(s, e)] for d in D_k[k] for (s, e) in S_ud[(u, d)]) >= 1 # relaxed - not 6hr shifts
        for d in D: # C12b: supervisor works at most at 1 location per day
            model += pulp.lpSum(worked_day[u][W_Su[u][0]][d] for u in U_store) <= 1
        for k in K: 
            model += pulp.lpSum(b[u][W_Su[u][0]][d][(s,e)] 
                                for u in U_store 
                                for d in D_k[k] 
                                for (s,e) in S_ud[(u, d)]) == len(U_store) # C12c: exactly 5 working days per week for supervisors
            model += (
                pulp.lpSum(
                    (e - s + 1) * b[u][W_Su[u][0]][d][(s, e)]
                    for u in U_store
                    for d in D_k[k]
                    for (s, e) in S_ud[(u, d)]
                )
                <= min(total_sup_hours, 30) # assign Supervisor minimum of total max hours or 30hrs
            )
        

    ## C13: rest weekend per 4 weeks
    if constraints_flag["C13"]:
        for u in U_store:
            for w in W_u[u]:
                if WeekendOnly[w] or (constraints_flag["C12"] and len(U_store) != 1 and Rolew[w] == "Supervisor"):
                    continue # if C12 on, supervisors handled below; if off, all workers + Javi for Catedral only included here
                model += pulp.lpSum(v[u][w][k] for k in K) >= 1 # C13a
                for k in K:
                    sat = 7 * k - 1
                    sun = 7 * k

                    sat_working = worked_day[u][w][sat]
                    sun_working = worked_day[u][w][sun]
                    model += (
                        v[u][w][k] >= 1 - sat_working - sun_working) # C13b
                    model += sat_working <= 1 - v[u][w][k] # C13c
                    model += sun_working <= 1 - v[u][w][k] # C13d


    ### C14: no Friday and Saturday closing shifts
    if constraints_flag["C14"]:
        fri = [d for d in D if weekday_of(d) == 5]
        for u in U_store:
            for w in W_u[u]:
                if constraints_flag["C12"] and len(U_store) != 1 and Rolew[w] == "Supervisor": # skip Supervisors and handle separately
                    continue
                for d in fri:
                    if (u, d) not in closing_slot or (u, d+1) not in closing_slot: # adjusted closing_shifts
                        continue
                    model += (
                        pulp.lpSum(b[u][w][d][(s, e)] for (s, e) in S_ud[(u, d)] if e == closing_slot[(u, d)]) 
                        + pulp.lpSum(b[u][w][d+1][(s, e)] for (s, e) in S_ud[(u, d)] if e == closing_slot[(u, d+1)]) <= 1 
                    )      


    ### C15: less than ten consecutive days limit
    if constraints_flag["C15"]:
        for u in U_store:
            for w in W_u[u]:
                if constraints_flag["C12"] and len(U_store) != 1 and Rolew[w] == "Supervisor": # skip Supervisors and handle separately
                    continue
                for d in D[:-9]:
                    model += pulp.lpSum(worked_day[u][w][d_] for d_ in range(d, d + 10)) <= 9


    ### SUPERVISOR-SPECIFIC CONSTRAINTS: ACTIVE IF C12 IS ACTIVE ###
    if constraints_flag["C12"] and len(U_store) != 1: # if C12 is not active, then no separate code for supervisors

        # C6-sup
        if constraints_flag["C6"]:
            for d in D[:-1]:
                for e, starts in forbidden.items():
                    model += (
                        pulp.lpSum(b[u][W_Su[u][0]][d][shift] for u in U_store for shift in S_ud[(u,d)] if shift[1] == e)
                        + pulp.lpSum(b[u][W_Su[u][0]][d+1][shift] for u in U_store for shift in S_ud[(u,d+1)] if shift[0] in starts) <= 1
                        )

        # C8-sup
        if constraints_flag["C8"]:
            for k in K:
                d_last = D_k[k][-1] # last day of week k (Sunday)
                
                # C8a-sup: at most 5 working days per week
                model += pulp.lpSum(worked_day[u][W_Su[u][0]][d] for u in U_store for d in D_k[k]) <= 5
                
                # C8b-sup: Fix z=0 on last day of week since a rest block cannot start here, as d+1 crosses into the next week
                model += z_sup[d_last] == 0

                # C8f: at least 1 set of rest days per week
                model += pulp.lpSum(z_sup[d] for d in D_k[k]) >= 1

                for d in D_k[k]:
                    if d == d_last: # can't start z on the Sunday/last day of week
                        continue
                    # C8c-e:
                    worked_day_d = pulp.lpSum(worked_day[u][W_Su[u][0]][d] for u in U_store)
                    worked_day_d1 = pulp.lpSum(worked_day[u][W_Su[u][0]][d+1] for u in U_store)

                    model += z_sup[d] <= 1 - worked_day_d # C8c - if z=1, d must be rest day
                    model += z_sup[d] <= 1 - worked_day_d1 # C8d - if z=1, d+1 must be rest day
                    model += z_sup[d] >= 1 - worked_day_d - worked_day_d1 # C8e - if d and d+1 rest days, z must be 1

                    # C8h: z's that cover day d; sum of rest days = rest block size
                    cover_z = [] # find which z variables could cover day d
                    if d != d_last and d in D_k[k]: # if d is not Sunday and is a day in that week k
                        cover_z.append(z_sup[d]) # rest block starts at d
                    if d - 1 in D_k[k] and d - 1 != d_last: # if d-1 is in that week and is not Sunday
                        cover_z.append(z_sup[d - 1]) # rest block starts at d-1

                    if cover_z:
                        model += 1 - worked_day_d <= pulp.lpSum(cover_z) # C8g: if d is rest day, at least z[d] or z[d-1] =1
                            # NOTE: this version allows Sunday to be an isolated rest day, but there's always at least another 2-day block
                    else:
                        pass # z[d] and z[d-1] = 0; could be d=d_last, cover_z>0 only if Saturday is a rest day

        # C13-sup
        if constraints_flag["C13"]:
            model += pulp.lpSum(v_sup[k] for k in K) >= 1 # C13a
            for k in K: # C13b
                sat = 7*k - 1
                sun = 7*k
                sat_sup_working = pulp.lpSum(worked_day[u][W_Su[u][0]][sat] for u in U_store)
                sun_sup_working = pulp.lpSum(worked_day[u][W_Su[u][0]][sun] for u in U_store)
                model += (v_sup[k] >= 1 - sat_sup_working - sun_sup_working) # C13b
                model += sat_sup_working <= 1 - v_sup[k] # C13c
                model += sun_sup_working <= 1 - v_sup[k] # C13d

        # C14-sup
        if constraints_flag["C14"]:
            for d in fri:
                sup_close_fri = pulp.lpSum(
                                    b[u][W_Su[u][0]][d][(s, e)] for u in U_store if (u, d) in closing_slot
                                    for (s, e) in S_ud[(u, d)] if e == closing_slot[(u, d)]
                                )
                sup_close_sat = pulp.lpSum(
                                    b[u][W_Su[u][0]][d+1][(s, e)] for u in U_store if (u, d+1) in closing_slot
                                    for (s, e) in S_ud[(u, d+1)] if e == closing_slot[(u, d+1)]
                                )
                model += sup_close_fri + sup_close_sat <= 1 

        # C15-sup
        if constraints_flag["C15"]:
            for d in D[:-9]:
                model += pulp.lpSum(worked_day[u][W_Su[u][0]][d_] for u in U_store for d_ in range(d, d + 10)) <= 9
        

    ### MISC print code - check # Variables and Constraints, which solvers are available for pulp and to you
    print("Variables:", len(model.variables())) # See number of variables in model
    print("Constraints:", len(model.constraints)) # See number of constraints in model
    solver_list = pulp.listSolvers()
    solver_list_me = pulp.listSolvers(onlyAvailable=True)
    print("Solvers in total: ", solver_list) # All solvers possible in PuLP
    print("Solvers for me: ", solver_list_me) # All available solvers


    # --------------------------------------------------------------------
    # Availability / Unavailability (hard)
    # --------------------------------------------------------------------

    if unavailability is None:
        unavailability = {}

    for u in U_store:
        for w in W_u[u]:
            uw = unavailability.get(w, {}) or {}
            unavail_days = set(uw.get("days", set()) or set())
            unavail_slots = set(uw.get("slots", set()) or set())

            for d in unavail_days:
                if d in D:
                    for (s, e) in S_ud[(u, d)]:
                        model += b[u][w][d][(s, e)] == 0

            for d in D:
                if d in unavail_days:
                    continue
                bad_t = {t for (dd, t) in unavail_slots if dd == d}
                if not bad_t:
                    continue
                for (s, e) in S_ud[(u, d)]:
                    if any(s <= t <= e for t in bad_t):
                        model += b[u][w][d][(s, e)] == 0



    # ------------------ Solve ------------------
    start_time = time.time()

    # ### CBC SOLVER ###
    # solver = pulp.PULP_CBC_CMD(msg=True, presolve=True, cuts=True, strong=True, threads=8, 
    #                            timeLimit=time_limit, gapRel=0.2)

    ### HiGHS SOLVER ###
    solver = pulp.HiGHS( # pulp.HiGHS_CMD
        mip=True, # solve as mixed integer programming
        msg=True, # display the message log
        threads=8, # max number of threads
        timeLimit=time_limit, # time limit for model to run
        gapRel=0.2, # relative gap tolerance for solver to stop
        options=[
            "parallel=on",
            "presolve=on",
            "mip_heuristic_effort=0.2", # from 0 to 1, higher means more time in heuristics and can find good solutions faster
            "mip_improving_solution_save=on", # captures best incumbent even if kill run early
            "mip_detect_symmetry=on", # have symmetry as workers are interchangeable and can have similar shift patterns; HiGHS can break symmetry and cut search space
            "random_seed=1",
        ],
    )

    # ### GUROBI SOLVER ###
    # solver = pulp.GUROBI(
    #     msg=True, # display the message log
    #     mip=True, # solve as mixed integer programming
    #     timeLimit=time_limit, # time limit for model to run
    #     gapRel=0.2, # relative gap tolerance for solver to stop
    #     threads=8, # max number of threads
    #     Presolve=2, # aggressive presolve
    #     Heuristics=0.2, # 20% heuristic effort
    #     Symmetry=2, # have symmetry as workers are interchangeable and can have similar shift patterns
    #     Seed=1, 
    #     MIPFocus=1, # prioritize finding good feasible solutions quickly over proving optimality
    # )

    model.solve(solver)
    end_time = time.time()

    status_str = pulp.LpStatus.get(model.status, str(model.status))

    # ✅ If infeasible (or no feasible found within time), return required message
    if status_str in {"Infeasible", "Undefined", "Not Solved"} or model.status == pulp.LpStatusInfeasible:
        return {
            "status": "NO FEASIBLE SOLUTION WAS FOUND",
            "objective": None,
            "elapsed_time": end_time - start_time,
            "schedule": []
        }

    # Otherwise return schedule
    shift_schedule = []
    for u in U_store:
        for w in W_u[u]:
            for d in D:
                for (s, e) in S_ud[(u, d)]:
                    if pulp.value(b[u][w][d][(s, e)]) > 0.5:
                        shift_schedule.append((u, w, d, s, e))
                    
    # Managerial coverage
    shift_adult = [] # ILM: =1 if no shift manager is assigned
    for u in U_store:
        for d in D:
            for t in S_demand[(u, d)]:
                if pulp.value(ILM[u][d][t]) > 0.5:
                    shift_adult.append((u, d, t))
    shift_adult = list(shift_adult)

    onduty_adult = []
    for d in D:
        slots = sorted({t for u in U_store for t in S_demand[(u, d)]})
        for t in slots:
            if pulp.value(CLM[d][t]) > 0.5:
                onduty_adult.append((d, t))
    onduty_adult = list(onduty_adult)

    return {
        "status": "Optimal" if status_str == "Optimal" else status_str,
        "objective": pulp.value(model.objective),
        "elapsed_time": end_time - start_time,
        "shift_schedule": shift_schedule, 
        "shift_adult": shift_adult, 
        "onduty_adult": onduty_adult, 
        "mgmt_shift": mgmt_shift, 
        "mgmt_cov": mgmt_cov
    }
