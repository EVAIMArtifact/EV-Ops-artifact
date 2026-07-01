import numpy as np
from sklearn.preprocessing import RobustScaler
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from datetime import datetime
import time
from utils.timeseries import preprocess, preprocess_testdata
from utils.utils import pot, dump_json
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm
from os.path import abspath, basename, dirname, exists, join
from configs import SEQ_LEN, RESULT_PATH, BATCH_SIZE
import os


from models.IdentityModel import IdentityModel
from models.MeanModel import MeanModel
from models.RandomMode import RandomModel
from models.BaroModel import BaroModel
from models.NSigmaModel import NsigmaModel
from models.LFTSAD import Model as LFTSAD
from models.SFlexRCA import Model as SFlexRCA, OrthTransform

# =========================
# POT algorithm wrapper
# =========================
def compute_pot_scores(errors, risk=1e-2, init_level=0.98, num_candidates=100, epsilon=1e-8):
    """
    Compute POT-based anomaly scores from reconstruction errors.
    Includes safeguards for tiny values and fallback to quantile.
    """
    errors = np.asarray(errors).ravel()

    # Rescale if values are too small
    if errors.max() < 1e-6:
        errors = errors * 1e6

    try:
        # POT thresholding
        z, t = pot(errors,
                   risk=risk,
                   init_level=init_level,
                   num_candidates=num_candidates,
                   epsilon=epsilon)
    except Exception as e:
        # Fallback: simple quantile threshold
        z = np.quantile(errors, 1 - risk)
        t = np.where(errors > z)[0]

    # Normalize scores
    scores = np.maximum(0, errors - z)
    if scores.max() > 0:
        scores = scores / scores.max()

    return scores, z, t



class CONFIG:  # NOTE
    """Dataclass with app parameters"""

    def __init__(self):
        pass

    # You must change this to the filename you wish to use as input data!
    # data_filename = "alarm.csv"

    # Epochs
    epochs = 100

    # Batch size (note: should be divisible by sample size, otherwise throw an error)
    batch_size = 256

    # Learning rate (baseline rate = 1e-3)
    lr = 1e-3

    x_dims = 1
    z_dims = 1
    # data_variable_size = 12
    optimizer = "Adam"
    graph_threshold = 0.3
    tau_A = 0.0
    lambda_A = 0.0
    c_A = 1
    use_A_connect_loss = 0
    use_A_positiver_loss = 0
    # no_cuda = True
    encoder_hidden = 128
    decoder_hidden = 128
    temp = 0.5
    k_max_iter = 1
    encoder = "mlp"
    decoder = "mlp"
    no_factor = False
    encoder_dropout = 0.0
    decoder_dropout = (0.0,)
    h_tol = 1e-8
    lr_decay = 200
    gamma = 1.0
    prior = False


CONFIG.cuda = torch.cuda.is_available()
CONFIG.factor = not CONFIG.no_factor




# ========================================
# VAE utility functions
# ========================================
def get_triu_indices(num_nodes):  # NOTE
    """Linear triu (upper triangular) indices."""
    ones = torch.ones(num_nodes, num_nodes)
    eye = torch.eye(num_nodes, num_nodes)
    triu_indices = (ones.triu() - eye).nonzero().t()
    triu_indices = triu_indices[0] * num_nodes + triu_indices[1]
    return triu_indices


def get_tril_indices(num_nodes):  # NOTE
    """Linear tril (lower triangular) indices."""
    ones = torch.ones(num_nodes, num_nodes)
    eye = torch.eye(num_nodes, num_nodes)
    tril_indices = (ones.tril() - eye).nonzero().t()
    tril_indices = tril_indices[0] * num_nodes + tril_indices[1]
    return tril_indices


def get_offdiag_indices(num_nodes):  # NOTE
    """Linear off-diagonal indices."""
    ones = torch.ones(num_nodes, num_nodes)
    eye = torch.eye(num_nodes, num_nodes)
    offdiag_indices = (ones - eye).nonzero().t()
    offdiag_indices = offdiag_indices[0] * num_nodes + offdiag_indices[1]
    return offdiag_indices


def get_triu_offdiag_indices(num_nodes):  # NOTE
    """Linear triu (upper) indices w.r.t. vector of off-diagonal elements."""
    triu_idx = torch.zeros(num_nodes * num_nodes)
    triu_idx[get_triu_indices(num_nodes)] = 1.0
    triu_idx = triu_idx[get_offdiag_indices(num_nodes)]
    return triu_idx.nonzero()


def get_tril_offdiag_indices(num_nodes):  # NOTE
    """Linear tril (lower) indices w.r.t. vector of off-diagonal elements."""
    tril_idx = torch.zeros(num_nodes * num_nodes)
    tril_idx[get_tril_indices(num_nodes)] = 1.0
    tril_idx = tril_idx[get_offdiag_indices(num_nodes)]
    return tril_idx.nonzero()



if not exists(RESULT_PATH):
    os.makedirs(RESULT_PATH)
# =========================
# Main causal RCA function
# =========================
def RMDnet(data, testdata, dataset=None, model_class=None, model_config=None, **kwargs):
    
    data = preprocess(
        data=data, dataset=dataset, dk_select_useful=kwargs.get("dk_select_useful", False)
    )
    train_data = data.drop(columns=["timestamp"], errors="ignore")

    data_variable_size = train_data.shape[1]

    node_names = train_data.columns.to_list()

    # ----------------------------
    # Scaling (SECOND)
    # ----------------------------
    scale_ref = train_data.max()

    scale_ref[scale_ref == 0] = 1
    from sklearn.preprocessing import MinMaxScaler
    scaler = MinMaxScaler()
    columns_to_keep = train_data.columns.to_list()
    train_data = scaler.fit_transform(train_data)
    #train_data = train_data / scale_ref

    # =========================
    # Inside RMDnet, before training
    # =========================

    func =model_class
    model_config.enc_in = data_variable_size
    model_config.seq_len = SEQ_LEN
    model_config.c_out = model_config.enc_in
    if model_class == SFlexRCA:
        class DummyDataset:
            pass

        dummy_dataset = DummyDataset()
        dummy_dataset.data_dict = {
            "x_n_list": train_data  # (W=num_rows, V=num_features)
        }
        orth_transformer = OrthTransform(
            dataset_obj=dummy_dataset,
            device= "cpu" if not torch.cuda.is_available() else "cuda"
        )
        model_config.orth_transformer = orth_transformer
    device = torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
    )

    # -------------------------------------------------
    # Build model
    # -------------------------------------------------
    #not in identity or mean or random case
    if model_class not in [IdentityModel, MeanModel, RandomModel, BaroModel, NsigmaModel]:
        encoder = func(model_config).to(device)
        #initialize weights using Xavier initialization
        for m in encoder.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
        encoder = encoder.float()

        # =========================
        # Efficiency metrics (STATIC)
        # =========================

        num_params = sum(p.numel() for p in encoder.parameters())
        num_trainable_params = sum(p.numel() for p in encoder.parameters() if p.requires_grad)

        model_size_mb = sum(
            p.numel() * p.element_size() for p in encoder.parameters()
        ) / (1024 ** 2)

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()


        print(f"CUDA available: {torch.cuda.is_available()}")
        print(f"Model on: {next(encoder.parameters()).device}")
        optimizer = optim.Adam(encoder.parameters(), lr=CONFIG.lr)
    else:
        encoder = func(model_config)  # dummy model, no training
        num_params = 0
        num_trainable_params = 0
        model_size_mb = 0.0
    #scheduler = lr_scheduler.StepLR(optimizer, step_size=CONFIG.lr_decay, gamma=CONFIG.gamma)

    # Linear indices of an upper triangular mx, used for acc calculation
    # =========================
    # Triangular indices on GPU
    # =========================
    triu_indices = get_triu_offdiag_indices(data_variable_size).to(device)
    tril_indices = get_tril_offdiag_indices(data_variable_size).to(device)

    if CONFIG.cuda and model_class not in [IdentityModel, MeanModel, RandomModel, BaroModel, NsigmaModel]:
        encoder.cuda()
        #decoder.cuda()
        triu_indices = triu_indices.cuda()
        tril_indices = tril_indices.cuda()

    # ----------------------------
    # Precompute diffusion schedule once (outside training loop)
    # ----------------------------
    T = 100  # max diffusion steps
    device = torch.device("cuda" if CONFIG.cuda else "cpu")

    betas = torch.linspace(1e-4, 0.02, T, device=device)   # put directly on correct device
    alpha = 1.0 - betas


    timing_stats = {"enc": [], "dec": [], "loss": [], "back": []}

    # ===================================
    # training: simplified AE (reconstruction only)
    # ===================================
    def train(epoch, optimizer, batch_size=64):
        encoder.train()
        #decoder.train()


        # Create input and next-step pairs
        x_data = train_data[:-1]      # all except last
        next_data = train_data[1:]    # all except first

        tensor_x = torch.tensor(x_data, dtype=torch.float32).to(device)
        tensor_next = torch.tensor(next_data, dtype=torch.float32).to(device)

        #dataset = torch.utils.data.TensorDataset(tensor_x, tensor_next)
        #loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)
        
        
        seq_len = SEQ_LEN
        batch_size = BATCH_SIZE

        # sliding window
        x_seq = []
        next_seq = []

        for i in range(len(tensor_x) - seq_len):
            x_seq.append(tensor_x[i:i+seq_len])
            next_seq.append(tensor_next[i:i+seq_len])

        x_seq = torch.stack(x_seq)       # shape: (num_windows, seq_len, vars)
        next_seq = torch.stack(next_seq)

        dataset = torch.utils.data.TensorDataset(x_seq, next_seq)
        loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

        mse_loss = nn.MSELoss()
        total_loss = 0.0
        total_mse = 0.0
        num_samples = 0
        for batch in loader:
            # forward
            x, next = batch
            x = x.to(device).float()
            next = next.to(device).float()
            
            x = x.float().to(device)
            next = next.float().to(device)

            x_recon = encoder(x)
            if model_class == LFTSAD:
                series, prior, series_seq, prior_seq = x_recon
                p_seq = 0.5
                loss_mse = 0.0
                for u in range(len(prior)):
                    loss_mse += (p_seq * mse_loss(series_seq[u], prior_seq[u]) + (1 - p_seq) * mse_loss(
                        series[u], prior[u]))
            else:
                loss_mse = mse_loss(
                    x_recon.squeeze(1),
                    next.squeeze(1)
                )

            loss = loss_mse 
            
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_loss += loss.item() * len(x)
            total_mse += loss.item() * len(x)
            num_samples += len(x)

        avg_loss = total_loss / num_samples
        avg_mse = total_mse / num_samples

        print(f"Epoch {epoch:03d} | Loss={avg_loss:.6f}")

        return avg_loss, avg_mse

    # ===================================
    # main
    # ===================================

    # optimizer step on hyparameters
    c_A = CONFIG.c_A
    lambda_A = CONFIG.lambda_A
    h_A_new = torch.tensor(1.0)
    h_tol = CONFIG.h_tol
    k_max_iter = int(CONFIG.k_max_iter)

    E_loss = []
    N_loss = []
    M_loss = []
    train_start = time.time()
    # name of experiment for TensorBoard logging
    
    exp_name = "causalrca_experiment_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    #writer = SummaryWriter(log_dir=f"./runs/{exp_name}")

    # ---------------- simplified patience config ----------------
    patience = 10
    no_improve = 0

    best_loss = float("inf")
    global_step = 0

    # tolerance via quantization (NO epsilon comparisons)
    def loss_key(x, precision=4):
        return round(float(x), precision)
    total_epochs_used = 0
    # -----------------------------------------------------------
    if model_class in [IdentityModel, MeanModel, RandomModel, BaroModel, NsigmaModel]:
        print(f"Using {model_class.__name__} - skipping training loop.")
        num_params = 0
        num_trainable_params = 0
        model_size_mb = 0.0
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    else:
        try:
            for step_k in range(k_max_iter):

                for epoch in range(CONFIG.epochs):

                    avg_loss, avg_mse = train(epoch, optimizer)
                    total_epochs_used += 1
                    #if writer is not None:
                    #    writer.add_scalar("loss/ELBO", avg_loss, global_step)
                    #    writer.add_scalar("loss/MSE", avg_mse, global_step)
                    #    writer.add_scalar("h_A", h_A_new.item(), global_step)
                    #    writer.add_scalar("c_A", c_A, global_step)
                    #    writer.add_scalar("lambda_A", lambda_A, global_step)

                    global_step += 1

                    E_loss.append(avg_loss)
                    N_loss.append(avg_mse)
                    M_loss.append(avg_mse)

                    # ---------------- PATIENCE LOGIC ----------------
                    current = loss_key(avg_loss, precision=4)
                    best = loss_key(best_loss, precision=4)

                    if current < best:
                        best_loss = avg_loss
                        no_improve = 0
                    else:
                        no_improve += 1

                    if no_improve >= patience:
                        break

                # ---------------- Lagrangian update ----------------
                lambda_A += c_A * h_A_new.item()

                # ---------------- stopping conditions ----------------
                if h_A_new.item() <= h_tol:
                    break

                # optional: early stop if completely stuck
                if no_improve >= patience:
                    break

            print(
                f"[step_k={step_k}] "
                f"enc={np.mean(timing_stats['enc']):.4f}s, "
                f"dec={np.mean(timing_stats['dec']):.4f}s, "
                f"loss={np.mean(timing_stats['loss']):.4f}s, "
                f"back={np.mean(timing_stats['back']):.4f}s"
            )

        except KeyboardInterrupt:
            print("Interrupted cleanly")

    train_time = time.time() - train_start
    train_time_per_epoch = train_time / (total_epochs_used + 1) if total_epochs_used >= 0 else 0.0


# =========================================================================
    # FIXED: COMPUTE TRAINING RESIDUAL PROFILE GLOBAL STATS (ALIGNED WITH NEXT)
    # =========================================================================
    x_data = train_data[:-1]      
    next_data = train_data[1:]    # The actual target data space
    tensor_x = torch.tensor(x_data, dtype=torch.float32).to(device)    
    tensor_next = torch.tensor(next_data, dtype=torch.float32).to(device)
    seq_len = SEQ_LEN
    batch_size = BATCH_SIZE

    x_seq = []
    target_seq = []  # Track targets separately
    for i in range(len(tensor_x) - seq_len):
        x_seq.append(tensor_x[i:i+seq_len])
        target_seq.append(tensor_next[i:i+seq_len])

    if model_class not in [IdentityModel, MeanModel, RandomModel, BaroModel, NsigmaModel]:
        encoder.eval()
        with torch.no_grad():
            train_tensor = torch.stack(x_seq).to(device).float()
            train_target_np = torch.stack(target_seq).cpu().numpy()
            train_recon_list = []
            
            for start in range(0, len(train_tensor), batch_size):
                end = start + batch_size
                train_batch = train_tensor[start:end]
                
                if model_class == LFTSAD:
                    series, prior, series_seq, prior_seq = encoder(train_batch)
                    train_recon_batch = series[0] if isinstance(series, list) else series
                else:
                    train_recon_batch = encoder(train_batch)
                
                train_recon_list.append(train_recon_batch.cpu())
            
            train_recon_np = torch.cat(train_recon_list, dim=0).numpy()
            
            # FIXED: Extract only the exact forecasted step (index 0 or matching point)
            # if train_recon_np shape is (N, 1, V), squeeze or index it properly
            if train_recon_np.shape[1] == 1:
                train_residual = np.abs(train_recon_np[:, 0, :] - train_target_np[:, 0, :])
            else:
                train_residual = np.abs(train_recon_np[:, :seq_len, :] - train_target_np[:, :seq_len, :])
            
            # Global per-variable statistics
            train_res_mean = train_residual.mean(axis=0)
            train_res_std = train_residual.std(axis=0) + 1e-8
    else:
        train_res_mean = 0.0
        train_res_std = 1.0


    # =========================
    # Peak memory (GPU only)
    # =========================
    if torch.cuda.is_available():
        peak_memory_mb = torch.cuda.max_memory_allocated() / (1024 ** 2)
    else:
        peak_memory_mb = 0.0

    #writer.close()
    # just build on my code to apply POT for ranks 
    # ===================================
    # After training → reconstruction & POT scoring
    # ===================================
    #decoder.eval()
    seq_len = SEQ_LEN
    batch_size = BATCH_SIZE
    infer_start = time.time()
    results = {}
    for t in tqdm(testdata):
        name, test_df = t
        test_df = preprocess_testdata(
            data=test_df, columns_to_keep = columns_to_keep
        )
        test_df = scaler.transform(test_df)  # apply same scaling as training data
        #test_df = test_df / scale_ref  # apply same scaling as training data

        
        with torch.no_grad():
            if model_class in [IdentityModel, MeanModel, RandomModel, BaroModel, NsigmaModel]:
                pass
            else:
                encoder.eval()
            data_np = np.array(test_df) 
            num_samples, num_vars = data_np.shape
            if model_class not in [BaroModel, NsigmaModel]:
                # Build sliding windows for sequence input
    # Build sliding windows for sequence input (X) and forecasting target (Y)
                sequences = []
                targets = []
                # Stop 1 step early to allow target tracking alignment
                for i in range(num_samples - seq_len):
                    sequences.append(data_np[i:i+seq_len])
                    targets.append(data_np[i+1:i+seq_len+1]) # Timestep T+1 sequence
                    
                if len(sequences) == 0:  # Fallback for extremely short arrays
                    sequences = [data_np]
                    targets = [data_np]
                    
                sequences = np.stack(sequences, axis=0)  
                targets = np.stack(targets, axis=0)

                sequences_tensor = torch.tensor(sequences, dtype=torch.float32, device=device)
                num_sequences = sequences_tensor.size(0)

                recon_list = []
                mse = nn.MSELoss(reduction='none')
                
                for start in range(0, num_sequences, batch_size):
                    end = start + batch_size
                    x_batch = sequences_tensor[start:end]
                    if model_class == LFTSAD:
                        series, prior, series_seq, prior_seq = encoder(x_batch)
                        p_seq = 0.5
                        loss = 0.0
                        for u in range(len(prior)):
                            loss += ((1 - p_seq) * mse(series[u], prior[u]) + p_seq * mse(series_seq[u], prior_seq[u]))
                        recon_batch = loss
                    else:
                        recon_batch = encoder(x_batch)

                    recon_list.append(recon_batch.cpu()[:, :seq_len, :])

            # =========================================================================
            # FIXED: UNIFIED PROFILE SCORING PATHS
            # =========================================================================
            if model_class in [IdentityModel, MeanModel, RandomModel, BaroModel, NsigmaModel]:
                if model_class == RandomModel:
                    ed_scores = np.random.rand(data_variable_size)
                elif model_class == IdentityModel:
                    ed_scores = np.var(data_np, axis=0)
                elif model_class == MeanModel:
                    ed_scores = np.mean(np.abs(data_np - np.mean(data_np, axis=0)), axis=0)
                elif model_class == BaroModel:
                    ed_scores = model_class.compute_baro_scores(data_np,node_names)
                elif model_class == NsigmaModel:    
                    ed_scores = model_class.compute_nsigma_scores(data_np,node_names)
            else:
                recon_tensor = torch.cat(recon_list, dim=0)
                recon_np = recon_tensor.numpy()

                # 1. Align prediction errors precisely to forecasting targets
                if recon_np.shape[1] == 1:
                    residual_np = np.abs(recon_np[:, 0, :] - targets[:, 0, :])
                else:
                    residual_np = np.abs(recon_np[:, :seq_len, :] - targets[:, :seq_len, :])
                
                # 2. Score via the stable train profile variance
                residual_z = (residual_np - train_res_mean) / train_res_std
                
                # 3. FIXED: MEDIAN CASCAED FILTER
                # Using the median isolates the shared network ripple floor 
                # without letting downstream noisy variations corrupt the baseline.
                global_cascade_floor = np.median(residual_z, axis=-1, keepdims=True)
                
                # Subtracting the median strips away the common cascading trend. 
                # The service that breaks *worse* than the network floor retains its clean peak.
                filtered_residual_z = np.maximum(0, residual_z - global_cascade_floor)
                
                # 4. Extract maximum over timeline
                if filtered_residual_z.ndim == 3:
                    ed_scores = filtered_residual_z.max(axis=(0, 1))
                else:
                    ed_scores = filtered_residual_z.max(axis=0)
            #else:
            #    recon_tensor = torch.cat(recon_list, dim=0)
            #    recon_np = recon_tensor.numpy()
#
            #    # FIXED: Check shape dimension to prevent silent broadcasting errors
            #    if recon_np.shape[1] == 1:
            #        # Compare the 1-step forecast directly to the 1-step target point
            #        residual_np = np.abs(recon_np[:, 0, :] - targets[:, 0, :])
            #    else:
            #        residual_np = np.abs(recon_np[:, :seq_len, :] - targets[:, :seq_len, :])
            #    
            #    # Normalize using global train stats (now safely matching shape (num_vars,))
            #    residual_z = (residual_np - train_res_mean) / train_res_std
            #    res_flat = residual_z.reshape(-1, data_variable_size)
            #    
            #    scores = []
            #    for i in range(data_variable_size):
            #        pot_val, _, _ = compute_pot_scores(
            #            res_flat[:, i],  
            #            risk=getattr(CONFIG, "pot_risk", 1e-2),
            #            init_level=getattr(CONFIG, "pot_init_level", 0.98),
            #            num_candidates=getattr(CONFIG, "pot_num_candidates", 10),
            #            epsilon=getattr(CONFIG, "pot_epsilon", 1e-8),
            #        )
            #        scores.append(pot_val)
#
            #    ed_scores = np.array([val.mean() for val in scores])

            baro_scores = np.zeros_like(ed_scores)
            hybrid_scores = ed_scores
            # === Rank variables ===
            ranks = list(zip(node_names, hybrid_scores))
            ranks.sort(key=lambda x: x[1], reverse=True)
            ranks = [x[0] for x in ranks]

        infer_time = time.time() - infer_start

        # =========================
        # Energy (simple NVML estimate)
        # =========================
        energy_joules = 0.0

        if torch.cuda.is_available():
            try:
                import pynvml
                pynvml.nvmlInit()
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)

                power_watts = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
                energy_joules = power_watts * (train_time + infer_time)

            except:
                energy_joules = 0.0

        data_path = name
        data_path = data_path.replace("14june_","")
        if data_path.endswith("_ramp"):
            data_path = data_path.replace("_ramp","")
        data_path = data_path.replace("_hog","")
        data_path = data_path.replace("_stress","")
        parts = data_path.split("_")
        if len(parts) == 2:
            service, metric = data_path.split("_")
            rp = join(RESULT_PATH, f"{service}_{metric}.json")
        else:
            service, metric, metric2 = data_path.split("_")
            rp = join(RESULT_PATH, f"{service}_{metric}_{metric2}.json")
        dump_json(filename=rp, data={0: ranks})
        results[name] = {
            "scores": hybrid_scores.tolist(),
            "ranks": ranks,
            "node_names": node_names,
            "ed_scores": ed_scores.tolist(),
            "baro_scores": baro_scores.tolist(),
            "infer_time": infer_time,
        }

    avg_total_infer_time_over_all_tests = np.mean([results[t]["infer_time"] for t in results])
    # === Final return dict ===
    return {
        "test_results": results,
        # efficiency metrics
        "train_time": train_time,
        "train_time_per_epoch": train_time_per_epoch,
        "total_epochs_used": total_epochs_used,
        "avg_total_infer_time_over_all_tests": avg_total_infer_time_over_all_tests,
        "num_params": num_params,
        "num_trainable_params": num_trainable_params,
        "model_size_mb": model_size_mb,
        "peak_memory_mb": peak_memory_mb,
        "energy_joules": energy_joules,
    }


