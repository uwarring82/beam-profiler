import {optimize} from './optimize.mjs';
self.onmessage = ({data}) => {
  try { self.postMessage({result:optimize(data.path, data.gap, data.catalog)}); }
  catch (error) { self.postMessage({error:error.message}); }
};
