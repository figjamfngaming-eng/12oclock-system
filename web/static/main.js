async function loadStats(){
  try{
    const s = await fetch('/api/stats').then(r=>r.json());
    const el = document.getElementById('stat-racers');
    if(el) el.textContent = s.racers;
  }catch(e){}

  try{
    const d = await fetch('/api/discord_stats').then(r=>r.json());
    const el = document.getElementById('stat-discord');
    const name = document.getElementById('stat-discord-name');
    if(el){
      if(d.ok){
        el.textContent = d.members;
        if(name) name.textContent = d.name || '';
      }else{
        el.textContent = '—';
        if(name) name.textContent = 'Enable Discord Server Widget';
      }
    }
  }catch(e){}
}
loadStats();
