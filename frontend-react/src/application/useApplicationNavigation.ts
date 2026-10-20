import {useEffect,useState} from 'react';
import {destination,type Destination} from './presentation';
export function useApplicationNavigation() {
  const [page, setPage] = useState<Destination>(() => destination(location.hash));
  useEffect(() => {
    const update = () => setPage(destination(location.hash));
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  return { page, navigate: (next: Destination) => { location.hash = next; setPage(next); } };
}
