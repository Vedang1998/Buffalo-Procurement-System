import hashlib,json,pathlib,subprocess,sys
sys.path.insert(0,'procurement/tools');import hosted_synthetic_native_inputs as n
v=n.require_complete_inventory();layout=n.execution_inventory();delta=n.installation_manifest()
commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],text=True).strip()
remap=lambda p:layout['renamed_logical_files'].get(p,p)
result={'schema':'BUFFALO_FIXED_NATIVE_INSTALLATION_DECISION_V1','candidate_commit':commit,'candidate_tree':tree,
 'status':'PROPOSED_COMPLETE_FIXED_NATIVE_GRAPH_NOT_INSTALLATION_PASS','implementation_manifest_sha256':n.INSTALLATION_MANIFEST_SHA256,
 'predecessor_manifest_sha256':n.PROPOSED_INVENTORY_SHA256,'per_file_delta':delta['additions'],
 'graph':v['derived_completeness'],'artifacts':v['artifacts'],'rows':list(layout['rows'].values()),
 'resolved_dependencies':{remap(k):{name:remap(target) for name,target in edges.items()} for k,edges in v['resolved_dependencies'].items()},
 'explicit_non_DT_NEEDED_roots':[remap(p) for p in v['explicit_non_DT_NEEDED_roots']],
 'mapped_data_roots':delta['mapped_data_roots'],'logical_aliases':layout['renamed_logical_files'],
 'mode_policy':'Authenticated member bytes unchanged; executable0555/data0444/new directories0555; backing top root0700. No host/shared chmod/chown.',
 'native_bytes':n.proposed_capacity(),'full_composition':json.loads(pathlib.Path('/tmp/buffalo-h123/'+commit[:7]+'-full-static-accounting.json').read_text()),
 'trust_root':delta['trust_root'],'snapshot_policy':delta['proposed_snapshot_policy'],'policy_acceptance':'OWNER_DECISION_NOT_YET_GIVEN',
 'provenance':{'python':'Exact CPython3.13.5 actions/python-versions3.13.5-15601068749 HTTPS publisher hash-list plus GitHub asset digest; signed build attestation NOT_ESTABLISHED',
 'ubuntu':'65 exact selected noble/noble-updates package digests linked to retained signed Releases and Packages indexes; signed build attestations NOT_ESTABLISHED',
 'trust_anchor':'Pinned official publisher HTTPS keyring, proposed immutable readonly fullysealed memfd. No trust inferred from writable target keyring.',
 'freshness':'Pinned Ubuntu noble static base + October29 noble-updates snapshot. No latest refresh or implicit owner acceptance.',
 'PGDG':'EXCLUDED; target-observed PGDG build is not an accepted substitute'},
 'entry_point':'procurement.tools.hosted_synthetic_native_route.installation_measurement_entry([repository,event,ref,commit],artifact_directory)',
 'activation':'UNCONDITIONAL_REFUSAL_BEFORE_SOURCE_OR_NATIVE_EFFECTS',
 'required_download_layout':'Unprivileged externally obtained public artifacts named by their exact SHA256 in artifact_directory; ubuntu-archive-keyring.gpg exact pin. No downloader, updater or installer script execution in this route.',
 'effects':{'installation':'One fresh /run/buffalo-native-<32hex>/view tree; verified platform native mkdir/dd/sha256sum/chmod/stat/find/rm/rmdir only. No recursive root deletion. Unprivileged stage per artifact and dpkg-deb fsys-tarfile/gzip decode, no package installation or scripts.',
 'measurement':'One exact private system-manager view/network-disabled unprivileged selected-Python child with externally bound PID/start, namespace, maps, backing, source and limits. No account creation before valid NativePhaseReadiness.',
 'service':'Existing dedicated per-run non-login UID/GID, original-host ownership/IPC/source checks; same exact request/view/interpreter handoff; fixed PG --version only. No caller command, network, database startup, secret or browser.',
 'cleanup':'Drain registered measurement/service consumers first; exact-owned inode/descendant checks; nonrecursive file batches<=64 and depth-group directories; foreign/pending/uncertain state retained. Temporary directory removal only when exact and empty.'},
 'limits':{'shared_work_seconds':60,'cleanup_seconds':5,'pg_phase_bytes':167772160,'diagnostic_application_pg_only_and_per_file_bytes':83886080,'stage_bytes':134217728,'entries':3000,'child_memory':268435456,'child_processes':2,'aggregate_service_memory':536870912,'aggregate_tasks':64,'namespace_seconds':20},
 'target_measurements':{'protected_selected_interpreter_READY_capture_final_maps':'NOT_RUN','native_pipeline_memory':'NOT_RUN','whole_service_memory':'NOT_RUN','fixed_postgres_execution':'NOT_RUN','real_installation_and_cleanup':'NOT_RUN'},
 'review':'PENDING_TARGETED_CLAUDE_DELTA_FROM34e8','owner_installation_decision':'NOT_GRANTED','credential_release':'DISABLED',
 'stage_labels':{'IMPLEMENTED':'graph,materialization,M1,observations,connected dormant route,cleanup','TESTED':'Source-bound controlled fixtures and unprivileged exact libc6 member; see validation record','REVIEWED':'34e8 historical dispositions only; new delta pending','TARGET_MEASURED':'NOT_RUN','ACTIVATED':'NO'}}
pathlib.Path('/tmp/buffalo-h123/oct31-native-installation-decision.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'artifacts':len(v['artifacts']),'files':len(layout['rows']),'native_bytes':layout['regular_bytes'],'sha256':hashlib.sha256(pathlib.Path('/tmp/buffalo-h123/oct31-native-installation-decision.json').read_bytes()).hexdigest()}))
