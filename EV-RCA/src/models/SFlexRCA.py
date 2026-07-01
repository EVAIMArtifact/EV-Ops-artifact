import torch
import torch.nn as nn
import numpy as np
import torch.nn.functional as F
from models.layers.RevIN import RevIN

class OrthTransform(nn.Module):
    def __init__(self, dataset_obj, device, eps=1e-3):
        super().__init__()

        self.device = device
        self.eps = eps

        print("Computing orthogonal whitening transform...")

        # ---- flatten full dataset ----
        x = dataset_obj.data_dict["x_n_list"]  # (S, W, V)
        x = x.reshape(-1, x.shape[-1])         # (S*W, V)

        # ---- covariance ----
        #cov = np.cov(x, rowvar=False)
#
        ## numerical stabilization
        #cov = cov + eps * np.trace(cov) / cov.shape[0] * np.eye(cov.shape[0])
#
        ## ---- SVD whitening (stable) ----
        #U, S, _ = np.linalg.svd(cov)
        #inv_sqrt = U @ np.diag(1.0 / np.sqrt(S + eps)) @ U.T
#
        #self.register_buffer(
        #    "Q",
        #    torch.tensor(inv_sqrt, dtype=torch.float32, device=device)
        #)
        # covariance
        cov = np.cov(x, rowvar=False)

        # Ledoit-style shrinkage
        lam = 0.1
        cov = (1 - lam) * cov + lam * np.eye(cov.shape[0])

        # eigendecomposition (better for symmetric PSD matrices)
        eigvals, eigvecs = np.linalg.eigh(cov)

        # avoid amplifying tiny eigenvalues
        eigvals = np.maximum(eigvals, eps)

        # whitening matrix
        inv_sqrt = eigvecs @ np.diag(1.0 / np.sqrt(eigvals)) @ eigvecs.T

        self.register_buffer(
            "Q",
            torch.tensor(inv_sqrt, dtype=torch.float32, device=device)
        )


    def forward(self, x):
        return x @ self.Q

    def inverse(self, x):
        return x @ self.Q.T
    
class TemporalBlock(nn.Module):
    def __init__(self, seq_len, rank):
        super().__init__()

        self.enc = nn.Linear(seq_len, rank, bias=False)
        self.dec = nn.Linear(rank, seq_len, bias=False)

        self.mix = nn.Sequential(
            nn.Linear(rank, rank, bias=False),
            nn.GELU(),
            nn.Linear(rank, rank, bias=False),
        )

        self.norm = nn.LayerNorm(rank)

    def forward(self, x):
        """
        x: [B, D, T]
        """

        z = self.enc(x)            # [B,D,R]

        h = self.mix(z)
        z = self.norm(z + h)

        return self.dec(z)        # [B,D,T]



class Model_good(nn.Module):

    def __init__(self, configs):
        super().__init__()

        self.seq_len = configs.seq_len
        self.pred_len = configs.pred_len
        self.enc_in = configs.enc_in

        self.length_ratio = (self.seq_len + self.pred_len) / self.seq_len

        self.revin = getattr(configs, "revin", True)
        if self.revin:
            self.revin_layer = RevIN(self.enc_in)

        self.orth = getattr(configs, "orth_transformer")
        self.orth_gate = nn.Parameter(
            torch.ones(self.seq_len)
        )
        self.alpha = nn.Parameter(torch.tensor(0.1))

        self.temporal_conv = nn.Conv1d(
            self.enc_in,
            self.enc_in,
            kernel_size=3,
            padding=1,
            groups=self.enc_in,
            bias=True #true for online-boutique
        )
        self.temporal_alpha = nn.Parameter(torch.tensor(0.1))
        
        

    def forward(self, x):
        # x : [B,T,C]
        
        if self.revin:
            x = self.revin_layer(x, 'norm')
        else:
            mean = x.mean(1, keepdim=True)
            std = torch.sqrt(
                x.var(1, keepdim=True, unbiased=False) + 1e-5
            )
            x = (x - mean) / std

        # ----------------------------------
        # Orth
        # ----------------------------------
        x_orth = self.orth(x)
        out = x + self.alpha * x_orth

        temp = self.temporal_conv(
            out.permute(0,2,1)
        ).permute(0,2,1)
        out = out + self.temporal_alpha * temp

        out = out * self.orth_gate[None,:,None]
        
        if self.revin:
            out = self.revin_layer(out, 'denorm')
        else:
            out = out * std + mean

        return out

class Model(nn.Module):

    def __init__(self, configs):
        super().__init__()

        self.seq_len = configs.seq_len
        self.enc_in = configs.enc_in

        self.revin = getattr(configs, "revin", True)
        if self.revin:
            self.revin_layer = RevIN(self.enc_in)

        # -------------------------
        # ORTH SETUP
        # -------------------------
        self.orth = configs.orth_transformer
        self.orth_type = configs.orth_type
        self.orth_residual = configs.orth_residual

        self.alpha = nn.Parameter(torch.tensor(0.1))
        self.orth_gate = nn.Parameter(torch.ones(self.seq_len))

        # scalar adaptive
        # channel adaptive
        if self.orth_type == "channel_adaptive":
            self.gate_net = nn.Sequential(
                nn.Linear(self.enc_in, self.enc_in),
                nn.Sigmoid()
            )
        if self.orth_residual == "low_rank":
            self.orth_mlp = nn.Sequential(
                nn.Linear(self.enc_in, self.enc_in // 2),
                nn.GELU(),
                nn.Linear(self.enc_in // 2, self.enc_in)
            )

        # -------------------------
        # TEMPORAL BRANCH
        # -------------------------
        self.temporal_type = configs.temporal_type
        self.temporal_alpha = nn.Parameter(torch.tensor(0.1))

        if self.temporal_type == "conv":
            self.temporal = nn.Conv1d(
                self.enc_in, self.enc_in,
                kernel_size=3, padding=1,
                groups=self.enc_in, bias=True
            )

        elif self.temporal_type == "mlp":
            hidden = max(4, self.seq_len // getattr(configs, "rank", 8))
            self.temporal = nn.Sequential(
                nn.Linear(self.seq_len, hidden, bias=False),
                nn.GELU(),
                nn.Linear(hidden, self.seq_len, bias=False)
            )

        elif self.temporal_type == "fft":
            self.freq_gate = nn.Parameter(torch.ones(self.seq_len // 2 + 1))

        elif self.temporal_type == "ssm":
            self.A = nn.Parameter(torch.ones(self.enc_in) * 0.9)
            self.B = nn.Parameter(torch.ones(self.enc_in) * 0.1)
            self.C = nn.Parameter(torch.ones(self.enc_in))

        elif self.temporal_type == "linear_attn":
            d = getattr(configs, "attn_dim", 16)
            self.q = nn.Linear(self.enc_in, d, bias=False)
            self.k = nn.Linear(self.enc_in, d, bias=False)
            self.v = nn.Linear(self.enc_in, self.enc_in, bias=False)

    # -------------------------
    # ORTH BRANCH
    # -------------------------
    def orth_branch(self, x):
        h = self.orth(x)

        # orth type
        if self.orth_type == "fixed":
            h = self.alpha * h
        elif self.orth_type == "channel_adaptive":
            g = self.gate_net(x.mean(1))[:, None, :]
            h = self.alpha * g * h

        # residual modulation
        if self.orth_residual == "simple":
            return h
        elif self.orth_residual == "low_rank":
            r = self.orth_mlp(x.mean(1))[:, None, :]
            h = h * (1 + r)
            return h


    # -------------------------
    # TEMPORAL BRANCH
    # -------------------------
    def temporal_branch(self, x):

        if self.temporal_type == "conv":
            return self.temporal(x.permute(0,2,1)).permute(0,2,1)

        elif self.temporal_type == "mlp":
            return self.temporal(x.permute(0,2,1)).permute(0,2,1)

        elif self.temporal_type == "fft":
            spec = torch.fft.rfft(x, dim=1)
            spec = spec * self.freq_gate[None,:,None]
            return torch.fft.irfft(spec, n=self.seq_len, dim=1)

        elif self.temporal_type == "ssm":
            h = torch.zeros_like(x[:,0])
            out = []
            for t in range(self.seq_len):
                h = self.A * h + self.B * x[:,t]
                out.append(h + self.C * x[:,t])
            return torch.stack(out, 1)

        elif self.temporal_type == "linear_attn":
            Q = F.elu(self.q(x)) + 1
            K = F.elu(self.k(x)) + 1
            V = self.v(x)

            KV = K.transpose(1,2) @ V
            Z = 1.0 / (Q @ K.sum(1,keepdim=True).transpose(1,2) + 1e-6)

            return (Q @ KV) * Z

    # -------------------------
    # FORWARD
    # -------------------------
    def forward(self, x):

        if self.revin:
            x = self.revin_layer(x, "norm")
        else:
            mean = x.mean(1, keepdim=True)
            std = (x.var(1, keepdim=True, unbiased=False) + 1e-5).sqrt()
            x = (x - mean) / std

        # -------------------------
        # FEATURE (ORTH)
        # -------------------------
        out = x + self.orth_branch(x)

        # -------------------------
        # TEMPORAL
        # -------------------------
        temp = self.temporal_branch(out)

        # -------------------------
        # FUSION
        # -------------------------
        out = (out + self.temporal_alpha * temp) * self.orth_gate[None,:,None]

        # -------------------------
        # DENORM
        # -------------------------
        if self.revin:
            return self.revin_layer(out, "denorm")

        return out * std + mean
    


    