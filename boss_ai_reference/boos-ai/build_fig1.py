import json, os

scene = {
    "version": "0.1",
    "metadata": {
        "title": "AutoLDiT Framework Overview",
        "created_by": "visiomaster",
        "style_profile": "paper_white",
        "fidelity": "approximate",
        "source_image": "D:\\研途\\真工作\\01-Study\\01-Project\\boos-ai\\总体框架图.png",
        "source_aspect_ratio": 1.676
    },
    "page": {
        "width": 1624, "height": 969,
        "units": "px", "origin": "top-left",
        "target_width_in": 16.24, "background": "#FFFFFF"
    },
    "nodes": [],
    "edges": [],
    "assets": []
}

n = scene["nodes"]
e = scene["edges"]

# === TITLE ===
n.append({"id":"title_stage2","type":"text_block","x":80,"y":28,"w":300,"h":28,
    "style":{"font_size":14,"font_weight":"bold","text_color":"#1a1a2e","font_family":"Microsoft YaHei UI"},
    "text":"Stage 2: Architecture Search"})
n.append({"id":"title_backbone","type":"text_block","x":700,"y":28,"w":480,"h":28,
    "style":{"font_size":14,"font_weight":"bold","text_color":"#184b99","font_family":"Microsoft YaHei UI","text_align":1},
    "text":"U-shaped Hybrid Attention Backbone (Shared by Supernet & Subnet)"})
n.append({"id":"title_stage3","type":"text_block","x":1300,"y":28,"w":280,"h":28,
    "style":{"font_size":14,"font_weight":"bold","text_color":"#1a1a2e","font_family":"Microsoft YaHei UI"},
    "text":"Stage 3: Subnet Retraining"})
n.append({"id":"subtitle_stage3","type":"text_block","x":1320,"y":52,"w":280,"h":22,
    "style":{"font_size":11,"text_color":"#666666","font_family":"Microsoft YaHei UI"},
    "text":"(Hierarchical Distillation)"})

# === LEFT: SEARCH SPACE ===
n.append({"id":"search_space_box","type":"group_container","x":40,"y":90,"w":320,"h":180,
    "style":{"fill_color":"#f5f7fb","border_color":"#7d95ae","border_width":1.5,"corner_radius":6},
    "label":{"text":"1. Search Space (Multi-scale)","style":{"font_size":10,"font_weight":"bold","text_color":"#184b99"},"position":"top_center","inside":True}})

for i,(l,v) in enumerate([
    ("Depth (Layers)","S:8 / B:14 / L:18"),
    ("# Heads","{4,8,12} / {8,12,16} / {16,20,24}"),
    ("MLP Ratio","Searchable ratio options"),
    ("Activation","GELU / SiLU / ReLU"),
    ("Kernel Size","{3x3, 5x5, 7x7}")
]):
    n.append({"id":f"ss_{i}","type":"text_block","x":55,"y":125+i*28,"w":290,"h":24,
        "style":{"font_size":9,"text_color":"#333333","font_family":"Consolas"},
        "text":f"  {l}:  {v}"})

n.append({"id":"supernet_box","type":"process_box","x":55,"y":260,"w":290,"h":45,
    "style":{"fill_color":"#d4e3f3","text_color":"#184b99","font_size":10,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},
    "text":"2. Supernet (Shared Weights)"})
n.append({"id":"supernet_training","type":"process_box","x":55,"y":315,"w":290,"h":40,
    "style":{"fill_color":"#e8f0fe","text_color":"#184b99","font_size":9,"corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},
    "text":"3. Supernet Training (Weight-sharing)"})

# === EVOLUTIONARY ALGORITHM ===
n.append({"id":"ea_box","type":"group_container","x":40,"y":370,"w":320,"h":280,
    "style":{"fill_color":"#fef4ea","border_color":"#c66c35","border_width":1.5,"corner_radius":6},
    "label":{"text":"Evolutionary Algorithm","style":{"font_size":11,"font_weight":"bold","text_color":"#8B4513"},"position":"top_center","inside":True}})

ea_labels = ["Population\nInitialization","Individual\nEvaluation","Selection","Crossover","Mutation"]
ea_ids = []
for i,label in enumerate(ea_labels):
    node_id = f"ea_{i}"
    ea_ids.append(node_id)
    n.append({"id":node_id,"type":"process_box",
        "x":60+(i%2)*140,"y":410+(i//2)*65,
        "w":110,"h":42,
        "style":{"fill_color":"#f5e6d3","text_color":"#8B4513","font_size":9,"corner_radius":12,"font_family":"Microsoft YaHei UI","text_align":1},
        "text":label})

e.append({"id":"ea_0_1","type":"arrow_connector","from":"ea_0","to":"ea_1","from_anchor":"right","to_anchor":"left","route":"horizontal","style":{"line_color":"#8B4513","line_width":1,"arrowhead":"end"}})
e.append({"id":"ea_1_2","type":"arrow_connector","from":"ea_1","to":"ea_2","from_anchor":"right","to_anchor":"left","route":"horizontal","style":{"line_color":"#8B4513","line_width":1,"arrowhead":"end"}})
e.append({"id":"ea_2_3","type":"arrow_connector","from":"ea_2","to":"ea_3","route":"orthogonal","style":{"line_color":"#8B4513","line_width":1,"arrowhead":"end"}})
e.append({"id":"ea_3_4","type":"arrow_connector","from":"ea_3","to":"ea_4","from_anchor":"right","to_anchor":"left","route":"horizontal","style":{"line_color":"#8B4513","line_width":1,"arrowhead":"end"}})
e.append({"id":"ea_4_0","type":"arrow_connector","from":"ea_4","to":"ea_0","route":"orthogonal","style":{"line_color":"#8B4513","line_width":1,"arrowhead":"end"}})

# === SURROGATE MODEL ===
n.append({"id":"surrogate_box","type":"group_container","x":40,"y":665,"w":320,"h":120,
    "style":{"fill_color":"#f0f7f0","border_color":"#599768","border_width":1.5,"corner_radius":6},
    "label":{"text":"Surrogate Model (MLP)","style":{"font_size":10,"font_weight":"bold","text_color":"#2d5a2d"},"position":"top_center","inside":True}})
n.append({"id":"perf_predictor","type":"process_box","x":55,"y":700,"w":130,"h":35,
    "style":{"fill_color":"#a8d8a8","text_color":"#2d5a2d","font_size":9,"corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Performance Predictor"})
n.append({"id":"ranking_score_box","type":"process_box","x":200,"y":700,"w":140,"h":35,
    "style":{"fill_color":"#a8d8a8","text_color":"#2d5a2d","font_size":9,"corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Ranking Score (Feedback)"})
n.append({"id":"arch_encoding","type":"process_box","x":55,"y":745,"w":285,"h":30,
    "style":{"fill_color":"#e8f0fe","text_color":"#184b99","font_size":9,"corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Architecture Encoding -> Performance Predictor"})

# === CENTER: U-SHAPED BACKBONE ===
n.append({"id":"backbone_bg","type":"group_container","x":390,"y":90,"w":730,"h":510,
    "style":{"fill_color":"#f9fbfd","border_color":"#7d95ae","border_width":1.5,"corner_radius":6}})

n.append({"id":"input_noise","type":"process_box","x":420,"y":115,"w":80,"h":30,
    "style":{"fill_color":"#d4e3f3","text_color":"#184b99","font_size":9,"corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Noised Latent"})
n.append({"id":"input_cond","type":"process_box","x":420,"y":150,"w":80,"h":30,
    "style":{"fill_color":"#d4e3f3","text_color":"#184b99","font_size":9,"corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Condition Embed"})

n.append({"id":"encoder_stack","type":"stacked_process","x":550,"y":105,"w":100,"h":140,
    "style":{"fill_color":"#c9d9de","text_color":"#1a1a2e","font_size":9,"corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Linear Transformer\nBlock\nBlock\nBlock","count":3,"stack_direction":"vertical"})

n.append({"id":"bottleneck_box","type":"process_box","x":700,"y":120,"w":120,"h":50,
    "style":{"fill_color":"#184b99","text_color":"#FFFFFF","font_size":9,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Bottleneck\n(Standard Transformer)"})
n.append({"id":"bottleneck_detail","type":"text_block","x":700,"y":175,"w":120,"h":18,
    "style":{"font_size":8,"text_color":"#555555","font_family":"Microsoft YaHei UI","text_align":1},"text":"Standard Transformer Block"})

n.append({"id":"decoder_stack","type":"stacked_process","x":880,"y":105,"w":100,"h":140,
    "style":{"fill_color":"#c9d9de","text_color":"#1a1a2e","font_size":9,"corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Linear Transformer\nBlock\nBlock\nBlock","count":3,"stack_direction":"vertical"})

n.append({"id":"ln_layer","type":"process_box","x":1020,"y":115,"w":60,"h":25,
    "style":{"fill_color":"#e8f0fe","text_color":"#184b99","font_size":8,"corner_radius":3,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Layer Norm"})
n.append({"id":"linear_reshape","type":"process_box","x":1020,"y":145,"w":60,"h":25,
    "style":{"fill_color":"#e8f0fe","text_color":"#184b99","font_size":8,"corner_radius":3,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Linear + Reshape"})
n.append({"id":"output_pred","type":"process_box","x":1020,"y":185,"w":60,"h":30,
    "style":{"fill_color":"#ffd700","text_color":"#1a1a2e","font_size":8,"font_weight":"bold","corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Predicted\nVector Field"})

# Skip connections
for i,pct in enumerate([0.2,0.5,0.8]):
    e.append({"id":f"skip_{i}","type":"arrow_connector",
        "from":"encoder_stack","from_anchor":f"right@{pct:.1f}",
        "to":"decoder_stack","to_anchor":f"left@{pct:.1f}",
        "route":"horizontal","style":{"line_color":"#7d95ae","line_width":1.2,"dash_pattern":"dash"}})

# Backbone flow
e.append({"id":"fuse_in","type":"arrow_connector","from":"input_noise","to":"encoder_stack","from_anchor":"right","to_anchor":"left","route":"horizontal","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"fuse_in2","type":"arrow_connector","from":"input_cond","to":"encoder_stack","from_anchor":"right","to_anchor":"left","route":"horizontal","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"enc_bn","type":"arrow_connector","from":"encoder_stack","to":"bottleneck_box","route":"vertical","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"bn_dec","type":"arrow_connector","from":"bottleneck_box","to":"decoder_stack","route":"vertical","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"dec_ln","type":"arrow_connector","from":"decoder_stack","to":"ln_layer","from_anchor":"right","to_anchor":"left","route":"horizontal","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"ln_lr","type":"arrow_connector","from":"ln_layer","to":"linear_reshape","route":"vertical","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"lr_out","type":"arrow_connector","from":"linear_reshape","to":"output_pred","route":"vertical","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})

# === RIGHT: STAGE 3 ===
n.append({"id":"optimal_arch_s2","type":"process_box","x":1260,"y":95,"w":200,"h":40,
    "style":{"fill_color":"#fff3cd","text_color":"#856404","font_size":10,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"1. Optimal Architecture\n(From Stage 2)"})
n.append({"id":"student_subnet","type":"process_box","x":1260,"y":155,"w":200,"h":45,
    "style":{"fill_color":"#d4e3f3","text_color":"#184b99","font_size":10,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"2. Subnet Retraining\nwith Hierarchical Distillation"})
n.append({"id":"trainable_note","type":"text_block","x":1260,"y":205,"w":200,"h":18,
    "style":{"font_size":8,"text_color":"#888888","font_family":"Microsoft YaHei UI","text_align":1},"text":"Student Subnet (Trainable)"})
n.append({"id":"teacher_model","type":"process_box","x":1260,"y":240,"w":200,"h":45,
    "style":{"fill_color":"#184b99","text_color":"#FFFFFF","font_size":10,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Teacher Model (Frozen)\nKnowledge Distillation"})
n.append({"id":"teacher_pretrained","type":"text_block","x":1260,"y":290,"w":200,"h":18,
    "style":{"font_size":8,"text_color":"#888888","font_family":"Microsoft YaHei UI","text_align":1},"text":"Teacher: Large Pretrained U-DiT"})

# Loss functions
n.append({"id":"loss_box","type":"group_container","x":1260,"y":320,"w":320,"h":100,
    "style":{"fill_color":"#f2edf6","border_color":"#5f5367","border_width":1.5,"corner_radius":6},
    "label":{"text":"Loss Functions","style":{"font_size":9,"font_weight":"bold","text_color":"#5f5367"},"position":"top_center","inside":True}})
for i,(name,desc) in enumerate([("L_FM","Flow Matching Loss"),("L_traj","Trajectory Distillation"),("L_dist","Distribution Distillation")]):
    n.append({"id":f"loss_{i}","type":"text_block","x":1270+(i%2)*150,"y":355+(i//2)*25,"w":140,"h":22,
        "style":{"font_size":10,"font_weight":"bold","text_color":"#5f5367","font_family":"Cambria Math"},"text":f"  {name} = {desc}"})

n.append({"id":"total_loss","type":"process_box","x":1260,"y":430,"w":310,"h":28,
    "style":{"fill_color":"#5f5367","text_color":"#FFFFFF","font_size":10,"font_weight":"bold","corner_radius":4,"font_family":"Cambria Math","text_align":1},"text":"L_retrain = L_dist + L_traj + L_FM"})
n.append({"id":"output_sbl","type":"process_box","x":1260,"y":475,"w":310,"h":40,
    "style":{"fill_color":"#184b99","text_color":"#FFFFFF","font_size":11,"font_weight":"bold","corner_radius":8,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Small / Base / Large Scale Architectures"})

# === BOTTOM: PERFORMANCE EVALUATION ===
n.append({"id":"perf_eval","type":"process_box","x":390,"y":620,"w":250,"h":45,
    "style":{"fill_color":"#599768","text_color":"#FFFFFF","font_size":10,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Performance Evaluation\n(Evaluation Results)"})
n.append({"id":"perf_eval_detail","type":"text_block","x":390,"y":670,"w":250,"h":18,
    "style":{"font_size":8,"text_color":"#555555","font_family":"Microsoft YaHei UI","text_align":1},"text":"Performance Evaluation -> Ranking Score"})

e.append({"id":"surrogate_to_rank","type":"arrow_connector","from":"ranking_score_box","to":"perf_eval","route":"orthogonal","style":{"line_color":"#599768","line_width":1.5,"dash_pattern":"dash","arrowhead":"end"}})
e.append({"id":"perf_to_ea","type":"arrow_connector","from":"perf_eval","to":"ea_1","route":"orthogonal","style":{"line_color":"#599768","line_width":1.5,"dash_pattern":"dash","arrowhead":"end"}})

# Cross connections
e.append({"id":"output_to_arch","type":"arrow_connector","from":"output_pred","to":"optimal_arch_s2","route":"orthogonal","style":{"line_color":"#184b99","line_width":1.5,"arrowhead":"end","dash_pattern":"dash"}})
e.append({"id":"arch_to_student","type":"arrow_connector","from":"optimal_arch_s2","to":"student_subnet","route":"vertical","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"teacher_to_loss","type":"arrow_connector","from":"teacher_model","to":"loss_box","route":"vertical","style":{"line_color":"#184b99","line_width":1,"arrowhead":"end"}})
e.append({"id":"loss_to_total","type":"arrow_connector","from":"loss_box","to":"total_loss","route":"vertical","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"total_to_output","type":"arrow_connector","from":"total_loss","to":"output_sbl","route":"vertical","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})

# === LEGEND ===
n.append({"id":"legend_box","type":"group_container","x":40,"y":810,"w":1540,"h":130,
    "style":{"fill_color":"#fafafa","border_color":"#cccccc","border_width":1,"corner_radius":4},
    "label":{"text":"Legend & Notation","style":{"font_size":11,"font_weight":"bold","text_color":"#333333"},"position":"top_center","inside":True}})

for i,(lbl,desc) in enumerate([
    ("Dataset","ImageNet -> Sampled Sub-architecture -> Supernet (Shared Weights)"),
    ("Weight Sharing","Selection / Frozen / Architecture Encoding"),
    ("Notation","L_FM: Diffusion Loss | L_traj: Feature Distillation Loss | L_dist: Consistency Loss"),
    ("Top-N","Top-N architectures predicted by surrogate -> Small / Base / Large scale architectures")
]):
    n.append({"id":f"legend_{i}","type":"text_block","x":60,"y":840+i*22,"w":900,"h":20,
        "style":{"font_size":9,"text_color":"#444444","font_family":"Microsoft YaHei UI"},"text":f"  [{lbl}]  {desc}"})

out_path = "D:/研途/真工作/01-Study/01-Project/boos-ai/visio_output/figure1/scene.json"
with open(out_path,'w',encoding='utf-8') as f:
    json.dump(scene,f,indent=2,ensure_ascii=False)
print(f"Figure 1 saved: {len(n)} nodes, {len(e)} edges")
