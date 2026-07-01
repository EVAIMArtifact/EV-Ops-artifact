import asyncio
import itertools
from tqdm import tqdm
import time
import os
import itertools
from dataclasses import dataclass
from typing import List
import playwright.async_api as pw

from playwright.async_api import async_playwright


NUM_USERS = 1000
MICROSERVICE_NAME = "robot-shop"
URL = f"LINK/{MICROSERVICE_NAME}.com/rca"
SCENARIO = {
    "online-boutique" : "Default shopper traffic",
    "sock-shop" : "Default shopper traffic",
    "robot-shop": "balanced"
}
SERVICES = {
    "online-boutique": ["frontend", "cartservice", "checkoutservice", "productcatalogservice", "currencyservice", "paymentservice", "shippingservice", "emailservice", "recommendationservice", "adservice", "redis-cart"],
    "sock-shop": ["front-end", "catalogue", "carts", "orders", "payment", "shipping", "user", "queue-master", "catalogue-db", "carts-db", "orders-db", "user-db", "session-db", "rabbitmq"],
    "robot-shop": ["web","catalogue","cart", "user", "payment", "shipping", "ratings", "dispatch", "mysql", "redis", "rabbitmq"],
}
INJECTION_DATA_DIR = f"src/data/{MICROSERVICE_NAME}/23June_WithDisk_RAMP_concurrent_{NUM_USERS}_users"
DOWNLOAD_TIME = 60*5  # seconds
CLEAR_TIME = 60  # seconds
SPAWN_RATE = 50  # users per second
WARMUP_TIME = 60  # seconds
FAULT_INJECTION_TIME = 60*2  # seconds
POST_FAULT_WAIT = 60  # seconds


# =========================
# 1. DATA MODELS
# =========================

@dataclass
class Fault:
    target: str
    fault_type: str
    duration: int = 10
    params: dict = None


@dataclass
class Experiment:
    name: str
    load_users: int
    load_scenario: str
    warmup: int
    faults: List[Fault]
    post_fault_wait: int


# =========================
# 2. EXPERIMENT RUNNER
# =========================
async def run_experiment(page, exp: Experiment):

    print(f"\n▶ Running experiment: {exp.name}")

    # Open UI with embedded auth
    await page.goto(URL, wait_until="domcontentloaded")

    # 0. Stop the running process, before injecting errors
    print("⏳ Stopping any existing load...")
    await page.click("#l-stop")
    await page.wait_for_timeout(CLEAR_TIME * 1000) # wait to clear any previous load

    # 2. Start warm-up load
    print(f"⏳ Starting warm-up load for {exp.warmup} seconds...")
    await page.select_option("#l-scenario", value=exp.load_scenario)
    await page.fill("#l-users", str(exp.load_users))
    await page.fill("#l-spawn", str(SPAWN_RATE))
    await page.click("#l-start")
    await page.wait_for_timeout(exp.warmup * 1000)

    # 3. Inject faults (can be multiple)
    for fault in exp.faults:
        
        # select target
        await page.select_option("#f-target", value=fault.target)

        # select fault type
        await page.select_option("#f-type", value=fault.fault_type)

        # CHECK IF THIS IS A DESIGNED RAMP-UP FAULT
        if fault.params and "ramp_to" in fault.params:
            p_key = fault.params["param_key"]
            start_val = fault.params["start_val"]
            end_val = fault.params["ramp_to"]
            steps = fault.params["steps"]
            
            # Distribute duration equally over steps
            duration_per_step = int(fault.duration / steps)
            val_increment = (end_val - start_val) / (steps - 1) if steps > 1 else 0

            print(f"📈 Ramping up {fault.fault_type} on {fault.target} over {steps} steps...")

            for step in range(steps):
                current_num = int(start_val + (step * val_increment))
                
                # Re-apply string units to match UI input specifications
                if fault.params["is_mb"]:
                    string_value = f"{current_num}MB"
                elif fault.params["is_ms"]:
                    string_value = f"{current_num}ms"
                else:
                    string_value = str(current_num)

                # Set step details
                await page.fill(f"#{p_key}", string_value)
                await page.fill("#f-duration", str(duration_per_step))
                
                # Fire the injection step
                await page.click("#f-inject")
                print(f"   Step {step+1}/{steps}: Set {p_key} to {string_value} for {duration_per_step}s")
                
                # Let this intensity bracket run completely before next step
                await page.wait_for_timeout(duration_per_step * 1000)
                
        else:
            # --- Standard Sudden Spike/Instantaneous Logic (Your Original Code) ---
            await page.fill("#f-duration", str(fault.duration))

            if fault.params:
                for param_key, param_value in fault.params.items():
                    if param_key not in ["pod_kill", "dependency_failure"]:
                        await page.fill(f"#{param_key}", str(param_value))

            # inject
            await page.click("#f-inject")

            # Wait out the explicit fault injection time block completely
            print(f" waiting for {fault.duration} seconds after injecting {fault.fault_type} on {fault.target}...")
            await page.wait_for_timeout(fault.duration * 1000)

    # 4. Observation window after last fault
    await page.wait_for_timeout(exp.post_fault_wait * 1000)

    # 5. Download metrics CSV
    async with page.expect_download() as download_info:
        await page.fill("#m-lookback", str(DOWNLOAD_TIME))  
        await page.click("#m-download")

    download = await download_info.value
    os.makedirs(INJECTION_DATA_DIR, exist_ok=True)
    await download.save_as(f"{INJECTION_DATA_DIR}/{exp.name}.csv")

    print(f"✔ Saved: {exp.name}.csv")


# =========================
# 2. EXPERIMENT RUNNER (Unified)
# =========================
async def run_experiment_concurrent(page, exp: Experiment):
    print(f"\n▶ Running experiment: {exp.name}")

    # Open UI with embedded auth
    await page.goto(URL, wait_until="domcontentloaded")

    # 0. Stop the running process, before injecting errors
    print("⏳ Stopping any existing load...")
    await page.click("#l-stop")
    await page.wait_for_timeout(CLEAR_TIME * 1000) # Wait to clear any previous load

    # 2. Start warm-up load
    print(f"⏳ Starting warm-up load for {exp.warmup} seconds...")
    await page.select_option("#l-scenario", value=exp.load_scenario)
    await page.fill("#l-users", str(exp.load_users))
    await page.fill("#l-spawn", str(SPAWN_RATE))
    await page.click("#l-start")
    await page.wait_for_timeout(exp.warmup * 1000)

    # 3. Inject faults (Handles Dual-Ramp or Standard Sequential Injections)
    if exp.faults:
        # Check if the first fault is configured as a ramp to trigger synchronized dual ramping
        if exp.faults[0].params and "ramp_to" in exp.faults[0].params:
            target = exp.faults[0].target
            steps = exp.faults[0].params["steps"]
            duration_per_step = int(exp.faults[0].duration / steps)
            
            await page.select_option("#f-target", value=target)
            print(f"📈 Stepping up CPU and Memory together on {target} over {steps} iterations...")
            
            for step in range(steps):
                print(f"\n▶ Iteration {step + 1}/{steps} (t = {duration_per_step * step}s)")
                
                # --- PHASE 1: CPU Injection for this step ---
                cpu_fault = next((f for f in exp.faults if f.fault_type == "cpu_hog"), None)
                if cpu_fault:
                    p_key = cpu_fault.params["param_key"]
                    start_val = cpu_fault.params["start_val"]
                    end_val = cpu_fault.params["ramp_to"]
                    val_increment = (end_val - start_val) / (steps - 1) if steps > 1 else 0
                    current_val = int(start_val + (step * val_increment))
                    
                    await page.select_option("#f-type", value="cpu_hog")
                    await page.fill(f"#{p_key}", str(current_val))
                    await page.fill("#f-duration", str(duration_per_step))
                    await page.click("#f-inject")
                    print(f"  ↳ Injected cpu_hog: {p_key}={current_val}")

                # --- PHASE 2: Memory Injection for this step ---
                mem_fault = next((f for f in exp.faults if f.fault_type == "mem_stress"), None)
                if mem_fault:
                    p_key = mem_fault.params["param_key"]
                    start_val = mem_fault.params["start_val"]
                    end_val = mem_fault.params["ramp_to"]
                    val_increment = (end_val - start_val) / (steps - 1) if steps > 1 else 0
                    current_val = int(start_val + (step * val_increment))
                    
                    string_value = f"{current_val}MB" if mem_fault.params["is_mb"] else str(current_val)
                    
                    await page.select_option("#f-type", value="mem_stress")
                    await page.fill(f"#{p_key}", string_value)
                    await page.fill("#f-duration", str(duration_per_step))
                    await page.click("#f-inject")
                    print(f"  ↳ Injected mem_stress: {p_key}={string_value}")

                # --- PHASE 3: Wait out this step's execution window ---
                print(f"⏳ Letting iteration {step + 1} run for {duration_per_step}s...")
                await page.wait_for_timeout(duration_per_step * 1000)

        else:
            # --- Fallback: Standard Sudden/Sequential Fault Logic ---
            for fault in exp.faults:
                await page.select_option("#f-target", value=fault.target)
                await page.select_option("#f-type", value=fault.fault_type)
                await page.fill("#f-duration", str(fault.duration))

                if fault.params:
                    for param_key, param_value in fault.params.items():
                        if param_key not in ["pod_kill", "dependency_failure"]:
                            await page.fill(f"#{param_key}", str(param_value))

                await page.click("#f-inject")
                print(f"🚀 Injected sudden {fault.fault_type} on {fault.target} for {fault.duration}s")
                await page.wait_for_timeout(fault.duration * 1000)

    # 4. Observation window after last fault
    await page.wait_for_timeout(exp.post_fault_wait * 1000)

    # 5. Download metrics CSV
    async with page.expect_download() as download_info:
        await page.fill("#m-lookback", str(DOWNLOAD_TIME))  
        await page.click("#m-download")

    download = await download_info.value
    os.makedirs(INJECTION_DATA_DIR, exist_ok=True)
    await download.save_as(f"{INJECTION_DATA_DIR}/{exp.name}.csv")

    print(f"✔ Saved: {exp.name}.csv")

# =========================
# 2.5 NORMAL DATA (NO FAULT) RUNNER
# =========================
async def run_experiment_normal(page, exp: Experiment):

    print(f"\n▶ Running experiment: {exp.name}")

    await page.goto(URL, wait_until="domcontentloaded")

    # 0. Stop the running process, before injecting errors
    print("⏳ Stopping any existing load...")
    await page.click("#l-stop")
    await page.wait_for_timeout(60 * 1000) # wait to clear any previous load

    # 1. Start warm-up load
    print(f"⏳ Starting warm-up load for {exp.warmup} seconds...")
    await page.select_option("#l-scenario", value=exp.load_scenario)
    await page.fill("#l-users", str(exp.load_users))
    await page.fill("#l-spawn", str(50))
    await page.click("#l-start")
    await page.wait_for_timeout(exp.warmup * 1000)

    NORMAL_COLLECTION_MINUTES = 180
    wait_time_normal = NORMAL_COLLECTION_MINUTES * 60

    print("\n⏳ Collecting normal traffic data...")

    start = time.time()

    step = 10
    steps = wait_time_normal // step

    for _ in tqdm(range(steps), desc="Normal data collection", unit="chunk"):
        await page.wait_for_timeout(step * 1000)

    async with page.expect_download() as download_info:
        await page.fill("#m-lookback", str(wait_time_normal))
        await page.click("#m-download")

    download = await download_info.value
    os.makedirs(f"data/{MICROSERVICE_NAME}", exist_ok=True)
    await download.save_as(
        f"data/{MICROSERVICE_NAME}/Normal_data_23June_{NUM_USERS}_users_{wait_time_normal//60}_minutes_Extension.csv"
    )

    print(f"✔ Saved: Normal_data_23June_{NUM_USERS}_users_{wait_time_normal//60}_minutes_Extension.csv")
    print(f"✔ Duration: {(time.time() - start)/60:.2f} min")

# =========================
# 3. EXPERIMENT GENERATORS
# =========================

SEVERITY = {
    "cpu_hog": {
        "fp-load": 50
    },

    "mem_stress": {
        "fp-size": "256MB"
    },

    "disk_stress": {
        "fp-size_mb": 50
    },

    "net_delay": {
        "fp-latency": "200ms"
    },

    "net_loss": {
        "fp-loss": 50
    },

    "pod_kill": {},

    "dependency_failure": {}
}

def generate_single_fault_experiments(targets):
    base_faults = ["cpu_hog", "mem_stress", "disk_stress"]#, "net_delay", "net_loss", "pod_kill", "dependency_failure"]
    rampable_faults = ["cpu_hog", "mem_stress", "disk_stress", "net_delay", "net_loss"]

    experiments = []

    for t, f in itertools.product(targets, base_faults):
        # 1. Standard sudden/constant intensity experiment
        #experiments.append(
        #    Experiment(
        #        name=f"14june_{t}_{f}",
        #        load_users=NUM_USERS,
        #        load_scenario=SCENARIO[MICROSERVICE_NAME],
        #        warmup=WARMUP_TIME,
        #        faults=[Fault(t, f, FAULT_INJECTION_TIME, SEVERITY[f].copy() if SEVERITY[f] else None)],
        #        post_fault_wait=POST_FAULT_WAIT,
        #    )
        #)
        # 2. Add the RAMP version if the fault type supports it
        if f in rampable_faults:
            base_params = SEVERITY[f].copy() if SEVERITY[f] else {}
            
            if base_params:
                # Identify the specific UI selector key (e.g., "fp-load", "fp-size")
                primary_key = list(base_params.keys())[0]
                raw_val = base_params[primary_key]
                
                # Parse out numbers from string suffixes if present
                is_mb = False
                is_ms = False
                
                if isinstance(raw_val, str):
                    if "MB" in raw_val:
                        target_num = int(raw_val.replace("MB", ""))
                        is_mb = True
                    elif "ms" in raw_val:
                        target_num = int(raw_val.replace("ms", ""))
                        is_ms = True
                    else:
                        target_num = int(raw_val)
                else:
                    target_num = int(raw_val)

                # Set up the execution instructions for your automate.py script
                ramp_params = {
                    "param_key": primary_key,
                    "start_val": 1 if not is_ms else 10, # Start at 1%, 1MB, or 10ms baseline
                    "ramp_to": target_num,
                    "steps": 4,
                    "is_mb": is_mb,
                    "is_ms": is_ms
                }
                
                experiments.append(
                    Experiment(
                        name=f"14june_{t}_{f}_ramp",
                        load_users=NUM_USERS,
                        load_scenario=SCENARIO[MICROSERVICE_NAME],
                        warmup=WARMUP_TIME,
                        faults=[Fault(t, f, FAULT_INJECTION_TIME, ramp_params)],
                        post_fault_wait=POST_FAULT_WAIT,
                    )
                )
        else:
            # For non-rampable faults, we can still add the standard version if not already added
            experiments.append(
                Experiment(
                    name=f"14june_{t}_{f}",
                    load_users=NUM_USERS,
                    load_scenario=SCENARIO[MICROSERVICE_NAME],
                    warmup=WARMUP_TIME,
                    faults=[Fault(t, f, FAULT_INJECTION_TIME, SEVERITY[f].copy() if SEVERITY[f] else None)],
                    post_fault_wait=POST_FAULT_WAIT,
                )
            )

    return experiments

def generate_dual_fault_single_service_experiments(targets):
    experiments = []

    fault_pairs = [
        #("cpu_hog", "mem_stress"),
        ("cpu_hog", "disk_stress"),
        ("mem_stress", "disk_stress"),
    ]

    for t in targets:
        for fault_a, fault_b in fault_pairs:

            ramp_faults_list = []

            for f in [fault_a, fault_b]:
                base_params = SEVERITY[f].copy() if SEVERITY[f] else {}

                if not base_params:
                    continue

                primary_key = list(base_params.keys())[0]
                raw_val = base_params[primary_key]

                is_mb = False

                if isinstance(raw_val, str) and "MB" in raw_val:
                    target_num = int(raw_val.replace("MB", ""))
                    is_mb = True
                else:
                    target_num = int(raw_val)

                ramp_params = {
                    "param_key": primary_key,
                    "start_val": 1,
                    "ramp_to": target_num,
                    "steps": 4,
                    "is_mb": is_mb,
                    "is_ms": False,
                }

                ramp_faults_list.append(
                    Fault(t, f, FAULT_INJECTION_TIME, ramp_params)
                )

            if len(ramp_faults_list) == 2:
                experiments.append(
                    Experiment(
                        name=f"14june_{t}_{fault_a}_{fault_b}_ramp",
                        load_users=NUM_USERS,
                        load_scenario=SCENARIO[MICROSERVICE_NAME],
                        warmup=WARMUP_TIME,
                        faults=ramp_faults_list,
                        post_fault_wait=POST_FAULT_WAIT,
                    )
                )

    return experiments


def generate_multi_fault_experiments():
    combos = [
        [("web", "cpu_hog"), ("catalogue", "cpu_hog")],
        [("web", "mem_stress"), ("catalogue", "mem_stress")],
        [("web", "cpu_hog"), ("web", "mem_stress")],
    ]

    experiments = []

    for combo in combos:
        faults = [Fault(t, f, 10) for t, f in combo]

        name = "__".join([f"14june_{t}_{f}" for t, f in combo])

        experiments.append(
            Experiment(
                name=name,
                load_users=NUM_USERS,
                load_scenario=SCENARIO[MICROSERVICE_NAME],
                warmup=10,
                faults=faults,
                post_fault_wait=40,
            )
        )

    return experiments


def build_all_experiments(targets):
    return generate_single_fault_experiments(targets=targets)# + generate_multi_fault_experiments()

def build_dual_fault_experiments(targets):
    return generate_dual_fault_single_service_experiments(targets=targets)
def save_config_and_severity():
    import json
    os.makedirs(INJECTION_DATA_DIR, exist_ok=True)
    config = {
        "URL": URL,
        "NUM_USERS": NUM_USERS,
        "TIMES": {
            "CLEAR_TIME": CLEAR_TIME,
            "SPAWN_RATE": SPAWN_RATE,
            "WARMUP_TIME": WARMUP_TIME,
            "FAULT_INJECTION_TIME": FAULT_INJECTION_TIME,
            "POST_FAULT_WAIT": POST_FAULT_WAIT,
            "DOWNLOAD_TIME": DOWNLOAD_TIME,
        },
        "SEVERITY": SEVERITY
    }
    with open(INJECTION_DATA_DIR + "/config.json", "w") as f:
        json.dump(config, f, indent=4)
# =========================
# 4. MASTER RUNNER
# =========================

async def run_all():
    targets = SERVICES[MICROSERVICE_NAME]  # Get the list of services for the selected microservice
    for t in targets:
        #experiments = build_all_experiments(targets=[t])
        experiments = build_dual_fault_experiments(targets=[t])

        async with pw.async_playwright() as p:
            # 1. Launch the browser
            browser = await p.chromium.launch(headless=False)
            
            # 2. CREATE THE CONTEXT WITH HTTP CREDENTIALS HERE
            context = await browser.new_context(
                http_credentials={"username": "admin", "password": "ceras"}
            )
            
            # 3. Create your page from this specific authenticated context
            page = await context.new_page()
            for exp in experiments:
                try:
                    await run_experiment_concurrent(page, exp)
                except Exception as e:
                    print(f"✖ Failed {exp.name}: {e}")
                    
            await browser.close()


async def run_normal():
    experiment = Experiment(
        name="normal_traffic",
        load_users=NUM_USERS,
        load_scenario=SCENARIO[MICROSERVICE_NAME],
        warmup=30,
        faults=[],
        post_fault_wait=0,
    )

    async with pw.async_playwright() as p:
        browser = await p.chromium.launch(headless=False)

        context = await browser.new_context(
            http_credentials={"username": "admin", "password": "ceras"}
        )

        page = await context.new_page()
        await run_experiment_normal(page, experiment)

        await browser.close()


# =========================
# 5. ENTRY POINT
# =========================

if __name__ == "__main__":
    asyncio.run(run_normal())
    #save_config_and_severity()
    #asyncio.run(run_all())
