from os.path import abspath, basename, dirname, exists, join

SEQ_LEN = 32
BATCH_SIZE = 64
RESULT_PATH = abspath(join(dirname(__file__), "..", "inner_results"))
OUT_PATH = abspath(join(dirname(__file__), "..", "out"))
SEED_NUM = 1
NUM_USERS = 1000


class FITSConfig:
    def __init__(self):
        self.seq_len = 0 #will be set in RMDnet 
        self.pred_len = 0

        self.enc_in = None

        # per-service frequency modeling (slower but better RCA)
        self.individual = False

        # lower frequency bias improves anomaly stability
        self.cut_freq = 5

        # optional extension hook (future causal layer)
        self.use_attention_mask = False


class FreTSConfig:
    def __init__(self):
        # set later by RMDNet
        self.seq_len = 0
        self.pred_len = SEQ_LEN

        # number of metrics / services
        self.enc_in = None

        # original FreTS
        self.embed_size = 128
        self.hidden_size = 256

        # enable frequency mixing across services
        self.channel_independence = '0'

        # frequency sparsity threshold
        self.sparsity_threshold = 0.01

        # complex weight initialization scale
        self.scale = 0.02

        # RCA enhancements
        self.use_correlation_embedding = True
        self.correlation_kernel = 3

        self.use_frequency_gate = True

        self.use_frequency_residual = True

        self.use_service_attention = False
        self.num_heads = 4

        self.use_graph_mixing = False

        self.use_multiscale_fft = False
        self.multiscale_freqs = [4, 8, 16]

class TimeMixerRCAConfig:
    def __init__(self):
        # --------------------------
        # sequence settings
        # --------------------------
        self.seq_len = SEQ_LEN
        self.label_len = 0
        self.pred_len = 0

        # --------------------------
        # model dimensions (REQUIRED)
        # --------------------------
        self.enc_in = None        # set at runtime
        self.c_out = None         # MUST be set = enc_in for reconstruction RCA

        self.d_model = 128
        self.d_ff = 256

        # --------------------------
        # architecture depth
        # --------------------------
        self.e_layers = 2

        self.down_sampling_window = 2
        self.down_sampling_layers = 2

        # --------------------------
        # channel behavior (VERY important for RCA)
        # --------------------------
        self.channel_independence = 0  # cross-service coupling

        # --------------------------
        # normalization (stability critical)
        # --------------------------
        self.use_norm = 1
        self.affine_norm = True

        # --------------------------
        # embedding
        # --------------------------
        self.embed = "timeF"
        self.freq = "s"
        self.dropout = 0.1

        # --------------------------
        # decomposition (core for TimeMixer++)
        # --------------------------
        self.moving_avg = 25
        self.decomp_method = "moving_avg"

        # --------------------------
        # task definition
        # --------------------------
        self.task_name = "anomaly_detection"
        self.use_future_temporal_feature = False
        self.down_sampling_method = "max"

        # --------------------------
        # RCA-specific controls (NOT model-required, but useful)
        # --------------------------
        self.top_k = 5
        self.residual_smoothing = True
        self.residual_smooth_window = 3
        self.score_normalization = "quantile"

        # optional experimental knobs
        self.metric_pruning = False


class LFTSADConfig:
    def __init__(self):

        # =========================
        # CORE SHAPE
        # =========================

        self.pred_len = 0

        # NOTE: model hardcodes channel = 55
        # so this is only for validation, not used by model
        self.enc_in = 55

        # =========================
        # PATCH CONFIG
        # =========================

        # point-level patch sizes
        self.patch_size = [4]

        # sequence-level patch grouping
        self.patch_seq = [4]

        # subsequence resolution inside each patch
        self.seq_size = 4
        self.win_size = self.seq_size           # must match input L

        # =========================
        # MODEL CAPACITY
        # =========================
        self.d_model = 128

        # =========================
        # BEHAVIOR FLAGS
        # =========================
        self.output_attention = True

        # =========================
        # OPTIONAL (unused by current model but useful later)
        # =========================
        self.individual = False
        self.cut_freq = 5
        self.use_attention_mask = False




class SFlexRCAConfig:
    def __init__(self):
        self.seq_len = 0 #will be set in RMDnet 
        self.pred_len = 0

        self.enc_in = None
        self.d_model = 12
        # per-service frequency modeling (slower but better RCA)
        self.individual = False

        # lower frequency bias improves anomaly stability
        self.cut_freq = 5

        # optional extension hook (future causal layer)
        self.use_attention_mask = False

        self.orth_transformer = None  # to be set at runtime with an instance of OrthTransform

        self.orth_type = None
        self.orth_res = None
        self.temporal_type = None
        

