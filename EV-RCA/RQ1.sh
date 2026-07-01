source ~/miniconda3/etc/profile.d/conda.sh
conda activate EVNname

SEEDS=(1 2 3)
SEQ_LEN=(32)
MICRO_SERVICE=("online-boutique" "sock-shop" "robot-shop")
MODEL_NAMES=("NSigma" "BARO" "FreTS" "LFTSAD" "fits" "SFlexRCA" "timemixerpp" "random")
DUAL_CASES=("0" "1")
for seed in "${SEEDS[@]}"; do
    for seq_len in "${SEQ_LEN[@]}"; do
        for dual_case in "${DUAL_CASES[@]}"; do
            echo "Running experiments with seed: $seed, seq_len: $seq_len and dual_case: $dual_case"
            for microservice in "${MICRO_SERVICE[@]}"; do
                echo "Running experiments for microservice: $microservice"
                for model_name in "${MODEL_NAMES[@]}"; do
                    echo Running Microservice: $microservice, Model: $model_name, Seed: $seed, Seq_len: $seq_len, Dual_case: $dual_case
                    python src/main.py --model_name "$model_name" --seed "$seed" --seq_len "$seq_len" --dual_case "$dual_case" --microservice_name "$microservice" --temporal_type "mlp" --orth_type "fixed" --orth_residual "simple"
                done
            done
        done
    done
done