import json

scene = {
    "version": "0.1",
    "metadata": {
        "title": "AutoLDiT Architecture Details",
        "created_by": "visiomaster",
        "style_profile": "paper_white",
        "fidelity": "approximate",
        "source_image": "D:\\研途\\真工作\\01-Study\\01-Project\\boos-ai\\总体框架图-2.png",
        "source_aspect_ratio": 1.676
    },
    "page": {"width": 1624, "height": 969, "units": "px", "origin": "top-left", "target_width_in": 16.24, "background": "#FFFFFF"},
    "nodes": [],
    "edges": [],
    "assets": []
}

n = scene["nodes"]
e = scene["edges"]

# === HEADER ===
header_items = [
    ("h_s1", 50, 20, 100, 26, {"font_size":13,"font_weight":"bold","text_color":"#023685","font_family":"Microsoft YaHei UI"}, "Stage 1"),
    ("h_ss", 150, 20, 400, 26, {"font_size":13,"font_weight":"bold","text_color":"#023685","font_family":"Microsoft YaHei UI"}, "Search Space Definition & Supernet Training"),
    ("h_backbone", 700, 20, 500, 26, {"font_size":13,"font_weight":"bold","text_color":"#184b99","font_family":"Microsoft YaHei UI","text_align":1}, "U-shaped Hybrid Attention Backbone"),
    ("h_backbone_sub", 700, 44, 500, 20, {"font_size":10,"text_color":"#666666","font_family":"Microsoft YaHei UI","text_align":1}, "(Shared by Supernet & Subnet)"),
    ("h_s3", 1200, 20, 80, 26, {"font_size":13,"font_weight":"bold","text_color":"#1a1a2e","font_family":"Microsoft YaHei UI"}, "Stage 3"),
    ("h_retrain", 1280, 20, 300, 26, {"font_size":13,"font_weight":"bold","text_color":"#1a1a2e","font_family":"Microsoft YaHei UI"}, "Subnet Retraining"),
    ("h_retrain_sub", 1280, 44, 300, 20, {"font_size":10,"text_color":"#666666","font_family":"Microsoft YaHei UI"}, "(Hierarchical Distillation)")
]
for i in header_items:
    n.append({"id":i[0],"type":"text_block","x":i[1],"y":i[2],"w":i[3],"h":i[4],"style":i[5],"text":i[6]})

# === LEFT PANEL ===
n.append({"id":"ss1","type":"process_box","x":40,"y":80,"w":220,"h":30,
    "style":{"fill_color":"#d4e3f3","text_color":"#023685","font_size":10,"font_weight":"bold","corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},
    "text":"1. Search Space (Multi-scale)"})
n.append({"id":"ss2","type":"process_box","x":270,"y":80,"w":240,"h":30,
    "style":{"fill_color":"#d4e3f3","text_color":"#023685","font_size":10,"font_weight":"bold","corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},
    "text":"2. Supernet (Shared Weights)"})

n.append({"id":"cfg_box","type":"group_container","x":40,"y":120,"w":490,"h":250,
    "style":{"fill_color":"#f5f7fb","border_color":"#9cb5c2","border_width":1.5,"corner_radius":4},
    "label":{"text":"Search Space Configuration","style":{"font_size":10,"font_weight":"bold","text_color":"#023685"},"position":"top_center","inside":True}})

config_items = [
    ("cfg_0", "Depth (Layers):  S:8 / B:14 / L:18"),
    ("cfg_1", "# Heads:  S:{4,8,12} / B:{8,12,16} / L:{16,20,24}"),
    ("cfg_2", "MLP Ratio:  Searchable feed-forward expansion"),
    ("cfg_3", "Activation:  GELU / SiLU / ReLU"),
    ("cfg_4", "Kernel Size:  DW Conv {3x3, 5x5, 7x7}")
]
for i,(k,v) in enumerate(config_items):
    n.append({"id":k,"type":"text_block","x":55,"y":155+i*42,"w":460,"h":36,
        "style":{"font_size":10,"text_color":"#333333","font_family":"Consolas"},"text":v})

n.append({"id":"ss3","type":"process_box","x":40,"y":380,"w":490,"h":32,
    "style":{"fill_color":"#e8f0fe","text_color":"#023685","font_size":10,"font_weight":"bold","corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},
    "text":"3. Supernet Training (ImageNet -> Sampled Sub-architecture -> Shared Weights)"})

n.append({"id":"teacher_info","type":"text_block","x":40,"y":425,"w":490,"h":22,
    "style":{"font_size":10,"text_color":"#555555","font_family":"Microsoft YaHei UI"},
    "text":"Teacher: Large Pretrained U-DiT (Frozen) -> Knowledge Distillation"})

# === CENTER: U-BACKBONE ===
n.append({"id":"backbone_box","type":"group_container","x":560,"y":80,"w":580,"h":370,
    "style":{"fill_color":"#f9fbfd","border_color":"#7d95ae","border_width":1.5,"corner_radius":6}})

n.append({"id":"enc_label","type":"text_block","x":580,"y":100,"w":200,"h":22,
    "style":{"font_size":10,"font_weight":"bold","text_color":"#1a1a2e","font_family":"Microsoft YaHei UI","text_align":1},"text":"Encoder (Downsampling)"})
n.append({"id":"enc_stack","type":"stacked_process","x":580,"y":125,"w":100,"h":120,
    "style":{"fill_color":"#c9d9de","text_color":"#1a1a2e","font_size":9,"corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1,"text_fit":"shrink_to_fit"},
    "text":"Linear Transformer\nBlock\nBlock\nBlock","count":3,"stack_direction":"vertical"})

n.append({"id":"bn_box","type":"process_box","x":730,"y":135,"w":120,"h":50,
    "style":{"fill_color":"#184b99","text_color":"#FFFFFF","font_size":9,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},
    "text":"Bottleneck\n(Standard Transformer)"})
n.append({"id":"bn_detail","type":"text_block","x":730,"y":190,"w":120,"h":18,
    "style":{"font_size":8,"text_color":"#555555","font_family":"Microsoft YaHei UI","text_align":1},"text":"Standard Transformer Block"})

n.append({"id":"dec_label","type":"text_block","x":920,"y":100,"w":200,"h":22,
    "style":{"font_size":10,"font_weight":"bold","text_color":"#1a1a2e","font_family":"Microsoft YaHei UI","text_align":1},"text":"Decoder (Upsampling)"})
n.append({"id":"dec_stack","type":"stacked_process","x":920,"y":125,"w":100,"h":120,
    "style":{"fill_color":"#c9d9de","text_color":"#1a1a2e","font_size":9,"corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1,"text_fit":"shrink_to_fit"},
    "text":"Linear Transformer\nBlock\nBlock\nBlock","count":3,"stack_direction":"vertical"})

# IO
n.append({"id":"input_noise","type":"process_box","x":580,"y":265,"w":80,"h":25,
    "style":{"fill_color":"#d4e3f3","text_color":"#184b99","font_size":8,"corner_radius":3,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Noised Latent"})
n.append({"id":"input_cond","type":"process_box","x":580,"y":295,"w":80,"h":25,
    "style":{"fill_color":"#d4e3f3","text_color":"#184b99","font_size":8,"corner_radius":3,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Condition Embed"})
n.append({"id":"ln_out","type":"process_box","x":960,"y":260,"w":70,"h":22,
    "style":{"fill_color":"#e8f0fe","text_color":"#184b99","font_size":8,"corner_radius":3,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Layer Norm"})
n.append({"id":"linear_out","type":"process_box","x":960,"y":288,"w":70,"h":22,
    "style":{"fill_color":"#e8f0fe","text_color":"#184b99","font_size":8,"corner_radius":3,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Linear+Reshape"})
n.append({"id":"pred_out","type":"process_box","x":960,"y":320,"w":70,"h":30,
    "style":{"fill_color":"#ffd700","text_color":"#1a1a2e","font_size":8,"font_weight":"bold","corner_radius":4,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Predicted\nVector Field"})

# Block legend
n.append({"id":"block_legend","type":"group_container","x":580,"y":360,"w":540,"h":80,
    "style":{"fill_color":"#fafafa","border_color":"#cccccc","border_width":1,"corner_radius":4}})
legend_items = [
    ("Linear Transformer Block", "Encoder/Decoder basic unit with linear attention"),
    ("Standard Transformer Block", "Bottleneck with standard self-attention"),
    ("Skip Connection", "Encoder-Decoder skip links for multi-scale features")
]
for i,(lbl,desc) in enumerate(legend_items):
    n.append({"id":"bl_%d"%i,"type":"text_block","x":595,"y":372+i*22,"w":500,"h":20,
        "style":{"font_size":9,"text_color":"#555555","font_family":"Microsoft YaHei UI"},
        "text":"  %s: %s"%(lbl,desc)})

# === RIGHT: STAGE 3 ===
n.append({"id":"opt_arch","type":"process_box","x":1200,"y":85,"w":220,"h":35,
    "style":{"fill_color":"#fff3cd","text_color":"#856404","font_size":10,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},
    "text":"1. Optimal Architecture\n(From Stage 2)"})
n.append({"id":"student","type":"process_box","x":1200,"y":135,"w":220,"h":45,
    "style":{"fill_color":"#d4e3f3","text_color":"#184b99","font_size":10,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},
    "text":"2. Subnet Retraining\nwith Hierarchical Distillation"})
n.append({"id":"student_note","type":"text_block","x":1200,"y":185,"w":220,"h":18,
    "style":{"font_size":8,"text_color":"#888888","font_family":"Microsoft YaHei UI","text_align":1},"text":"Student: Noised Latent -> Predicted Noise"})
n.append({"id":"teacher","type":"process_box","x":1200,"y":215,"w":220,"h":40,
    "style":{"fill_color":"#184b99","text_color":"#FFFFFF","font_size":10,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},
    "text":"Teacher Model (Frozen)"})

n.append({"id":"traj_label","type":"text_block","x":1200,"y":270,"w":110,"h":20,
    "style":{"font_size":10,"font_weight":"bold","text_color":"#5f5367","font_family":"Microsoft YaHei UI","text_align":1},"text":"Trajectory"})
n.append({"id":"traj_sub","type":"text_block","x":1200,"y":287,"w":110,"h":18,
    "style":{"font_size":8,"text_color":"#888888","font_family":"Microsoft YaHei UI","text_align":1},"text":"Distillation"})
n.append({"id":"dist_label","type":"text_block","x":1320,"y":270,"w":110,"h":20,
    "style":{"font_size":10,"font_weight":"bold","text_color":"#5f5367","font_family":"Microsoft YaHei UI","text_align":1},"text":"Distribution"})
n.append({"id":"dist_sub","type":"text_block","x":1320,"y":287,"w":110,"h":18,
    "style":{"font_size":8,"text_color":"#888888","font_family":"Microsoft YaHei UI","text_align":1},"text":"Distillation (L_dist)"})
n.append({"id":"fm_loss","type":"text_block","x":1200,"y":315,"w":250,"h":22,
    "style":{"font_size":10,"font_weight":"bold","text_color":"#5f5367","font_family":"Cambria Math"},"text":"L_FM = Flow Matching Loss"})
n.append({"id":"loss_total","type":"process_box","x":1200,"y":350,"w":300,"h":25,
    "style":{"fill_color":"#5f5367","text_color":"#FFFFFF","font_size":10,"font_weight":"bold","corner_radius":4,"font_family":"Cambria Math","text_align":1},
    "text":"L_retrain = L_dist + L_traj + L_FM"})

# === BOTTOM: STAGE 2 ARCHITECTURE SEARCH ===
n.append({"id":"s2_box","type":"group_container","x":40,"y":465,"w":1540,"h":200,
    "style":{"fill_color":"#fef9f5","border_color":"#c66c35","border_width":2,"corner_radius":6},
    "label":{"text":"Stage 2: Architecture Search (Evolutionary Algorithm + Surrogate Model)",
        "style":{"font_size":12,"font_weight":"bold","text_color":"#8B4513"},"position":"top_center","inside":True}})

n.append({"id":"ea_title","type":"process_box","x":60,"y":505,"w":160,"h":28,
    "style":{"fill_color":"#f5e6d3","text_color":"#8B4513","font_size":10,"font_weight":"bold","corner_radius":12,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Evolutionary Algorithm"})

ea_labels = ["Population","Individual\nEvaluation","Selection","Crossover","Mutation"]
ea_ids = []
for i,lbl in enumerate(ea_labels):
    nid = "ea2_%d"%i
    ea_ids.append(nid)
    n.append({"id":nid,"type":"process_box","x":60+i*140,"y":545,"w":100,"h":40,
        "style":{"fill_color":"#f5e6d3","text_color":"#8B4513","font_size":9,"corner_radius":12,"font_family":"Microsoft YaHei UI","text_align":1},"text":lbl})

for i in range(len(ea_labels)-1):
    e.append({"id":"ea2_%d_%d"%(i,i+1),"type":"arrow_connector","from":"ea2_%d"%i,"from_anchor":"right","to":"ea2_%d"%(i+1),"to_anchor":"left","route":"horizontal","style":{"line_color":"#8B4513","line_width":1,"arrowhead":"end"}})

n.append({"id":"supernet_eval","type":"process_box","x":60,"y":610,"w":200,"h":32,
    "style":{"fill_color":"#d4e3f3","text_color":"#184b99","font_size":9,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Supernet Evaluation (Shared Weights)"})
n.append({"id":"eval_results","type":"process_box","x":280,"y":610,"w":180,"h":32,
    "style":{"fill_color":"#e8f0fe","text_color":"#184b99","font_size":9,"corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Evaluation Results"})

n.append({"id":"surrogate_mlp","type":"process_box","x":700,"y":545,"w":200,"h":40,
    "style":{"fill_color":"#599768","text_color":"#FFFFFF","font_size":10,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Surrogate Model (MLP)"})
n.append({"id":"pred_topn","type":"process_box","x":700,"y":610,"w":200,"h":32,
    "style":{"fill_color":"#a8d8a8","text_color":"#2d5a2d","font_size":9,"font_weight":"bold","corner_radius":6,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Predicted Top-N Architectures"})
n.append({"id":"ranking_fb","type":"process_box","x":500,"y":550,"w":160,"h":28,
    "style":{"fill_color":"#a8d8a8","text_color":"#2d5a2d","font_size":9,"corner_radius":15,"font_family":"Microsoft YaHei UI","text_align":1},"text":"Ranking Score Feedback"})

# Edges for stage 2
e.append({"id":"e_sel_sup","type":"arrow_connector","from":"ea2_2","to":"supernet_eval","route":"orthogonal","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"e_sup_eval","type":"arrow_connector","from":"supernet_eval","from_anchor":"right","to":"eval_results","to_anchor":"left","route":"horizontal","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"e_eval_surr","type":"arrow_connector","from":"eval_results","from_anchor":"right","to":"surrogate_mlp","to_anchor":"left","route":"horizontal","style":{"line_color":"#599768","line_width":1.5,"arrowhead":"end"}})
e.append({"id":"e_surr_topn","type":"arrow_connector","from":"surrogate_mlp","to":"pred_topn","route":"vertical","style":{"line_color":"#599768","line_width":1,"arrowhead":"end"}})
e.append({"id":"e_surr_rank","type":"arrow_connector","from":"surrogate_mlp","from_anchor":"left","to":"ranking_fb","to_anchor":"right","route":"horizontal","style":{"line_color":"#599768","line_width":1,"dash_pattern":"dash","arrowhead":"end"}})
e.append({"id":"e_rank_ea","type":"arrow_connector","from":"ranking_fb","from_anchor":"left","to":"ea2_1","to_anchor":"right","route":"horizontal","style":{"line_color":"#599768","line_width":1,"dash_pattern":"dash","arrowhead":"end"}})

# Backbone flow edges
e.append({"id":"enc_bn","type":"arrow_connector","from":"enc_stack","to":"bn_box","route":"vertical","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
e.append({"id":"bn_dec","type":"arrow_connector","from":"bn_box","to":"dec_stack","route":"vertical","style":{"line_color":"#555555","line_width":1,"arrowhead":"end"}})
for i,p in enumerate([0.2,0.5,0.8]):
    e.append({"id":"sk_%d"%i,"type":"arrow_connector","from":"enc_stack","from_anchor":"right@%.1f"%p,"to":"dec_stack","to_anchor":"left@%.1f"%p,"route":"horizontal","style":{"line_color":"#7d95ae","line_width":1,"dash_pattern":"dash"}})

out = "D:/研途/真工作/01-Study/01-Project/boos-ai/visio_output/figure2/scene.json"
with open(out,"w",encoding="utf-8") as f:
    json.dump(scene,f,indent=2,ensure_ascii=False)
print("OK: %d nodes, %d edges"%(len(n),len(e)))
