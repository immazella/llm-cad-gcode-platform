import type { MachiningConfig, MachineProfile, ModelSummary, Material } from '../types';
// Deterministic test fixtures, not responses from a live server.
export const config: MachiningConfig = {
  profile_id:'fanuc_vmc', setup:{work_offset:null,wcs:54,origin_xy:'center',material:'al6061',stock_side:0,stock_top:0},
  tools:[{number:1,type:'endmill',diameter:6,description:'Özel takım adı',length:50,flutes:2,flute_length:20,stickout:30,shank_diameter:6,corner_radius:null,tool_material:'carbide',coating:'',length_offset:1,diameter_offset:null,holder:'',vendor:'',product_id:'',presets:[]}],
  cutting:{spindle_rpm:3000,feed_rate:500,plunge_rate:100,depth_per_pass:1,stepover_ratio:0.4,drill_rpm:1000,drill_feed:100,peck_depth:1,safe_z:5,retract_z:2,breakthrough:0.5,coolant:'off'},
  cutting_mode:'auto',operations:['pocket'],mill_tool:1,
  op_settings:Object.fromEntries(['face','drill','helical','pocket','engrave','contour'].map(key=>[key,{enabled:true,tool:null,preset:null,rpm:null,feed:null,plunge:null,stepdown:null,stepover_ratio:null,stock_xy:0,stock_z:0,finish_pass:false,finish_allowance:0.1,entry:'ramp',ramp_angle:2,cycle:'auto',peck:null}])) as MachiningConfig['op_settings'],
};
export const profile: MachineProfile = {
  id:'fanuc_vmc',name:'Dik işleme merkezi — Fanuc 0i-MF tarzı (örnek)',description:'',axes:3,controller:'fanuc',post:'fanuc',travel:{X:{min:0,max:500},Y:{min:0,max:500},Z:{min:-300,max:0}},max_feed:5000,rapid_feed:10000,max_spindle:8000,min_spindle:100,tool_changer:true,max_tools:20,tool_change_time_s:6,canned_cycles:true,requires_tool_length_comp:true,program_number:true,default_work_offset:[100,100,-100],notes:[],manufacturer:'',machine_model:'',serial:'',spindle_power_kw:null,max_tool_diameter:null,max_tool_length:null,coolant_modes:{flood:'M8'},work_offsets:[54],post_options:{line_numbers:false,line_increment:10,comments:true,home_at_end:'none',arc_format:'ijk'},builtin:true,
};
export const model: ModelSummary = {model_id:'m1',name:'Benim parçam',kind:'parametric',version:1,base:{},bbox:{min:[0,0,0],max:[60,60,10],size:[60,60,10]},volume_mm3:36000,face_count:6,features:[],holes:[],pockets:[],can_undo:false,log:[]};
export const material: Material = {id:'al6061',name:'Alüminyum 6061-T6',group:'N',group_tr:'demir dışı',vc_carbide_mill:300,vc_hss_drill:60};
